"""Unit tests for :mod:`tradinglab.drawings.model`."""
from __future__ import annotations

import math
import re

import pytest

from tradinglab.drawings import model as M
from tradinglab.drawings.model import (
    DEFAULT_COLOR,
    DEFAULT_STYLE,
    DEFAULT_WIDTH,
    DRAWING_KIND_HLINE,
    MAX_WIDTH,
    VALID_STYLES,
    Drawing,
    make_hline_drawing,
    normalize_ticker,
    snap_price_to_grid,
)

_UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


# ---------------------------------------------------------------
# normalize_ticker
# ---------------------------------------------------------------

class TestNormalizeTicker:
    def test_uppercases(self):
        assert normalize_ticker("amd") == "AMD"

    def test_strips_whitespace(self):
        assert normalize_ticker("  amd  ") == "AMD"
        assert normalize_ticker("\tamd\n") == "AMD"

    def test_none_to_empty(self):
        assert normalize_ticker(None) == ""

    def test_non_string_cast(self):
        assert normalize_ticker(123) == "123"

    def test_empty_string_stays_empty(self):
        assert normalize_ticker("") == ""
        assert normalize_ticker("   ") == ""


# ---------------------------------------------------------------
# make_hline_drawing
# ---------------------------------------------------------------

class TestMakeHlineDrawing:
    def test_basic_fields(self):
        d = make_hline_drawing("AMD", 92.5)
        assert d.kind == DRAWING_KIND_HLINE
        assert d.ticker == "AMD"
        assert d.price == 92.5
        assert d.color == DEFAULT_COLOR
        assert d.width == DEFAULT_WIDTH
        assert d.style == DEFAULT_STYLE
        assert d.label == ""
        assert d.extra == {}

    def test_generates_uuid_hex(self):
        d = make_hline_drawing("AMD", 1.0)
        assert _UUID_HEX_RE.match(d.id), f"id is not uuid hex: {d.id!r}"

    def test_unique_ids(self):
        ids = {make_hline_drawing("AMD", 1.0).id for _ in range(50)}
        assert len(ids) == 50

    def test_generates_iso_created_at(self):
        d = make_hline_drawing("AMD", 1.0)
        assert _ISO_RE.match(d.created_at), f"bad created_at: {d.created_at!r}"

    def test_normalizes_ticker(self):
        d = make_hline_drawing(" amd ", 1.0)
        assert d.ticker == "AMD"

    def test_invalid_style_falls_back_to_solid(self):
        d = make_hline_drawing("AMD", 1.0, style="bogus")
        assert d.style == DEFAULT_STYLE

    def test_invalid_style_case_insensitive(self):
        d = make_hline_drawing("AMD", 1.0, style="DASHED")
        assert d.style == "dashed"

    def test_non_positive_width_falls_back(self):
        assert make_hline_drawing("AMD", 1.0, width=0).width == DEFAULT_WIDTH
        assert make_hline_drawing("AMD", 1.0, width=-1).width == DEFAULT_WIDTH

    def test_non_numeric_width_falls_back(self):
        # ``make_hline_drawing`` accepts the value and lets _coerce_width
        # downgrade silently.
        assert make_hline_drawing("AMD", 1.0, width="oops").width == DEFAULT_WIDTH

    def test_empty_color_falls_back(self):
        d = make_hline_drawing("AMD", 1.0, color="")
        assert d.color == DEFAULT_COLOR

    def test_custom_id_preserved(self):
        d = make_hline_drawing("AMD", 1.0, drawing_id="manual-id")
        assert d.id == "manual-id"

    def test_custom_created_at_preserved(self):
        d = make_hline_drawing("AMD", 1.0, created_at="2026-01-01T00:00:00")
        assert d.created_at == "2026-01-01T00:00:00"

    def test_label_passes_through(self):
        d = make_hline_drawing("AMD", 1.0, label="stop")
        assert d.label == "stop"

    def test_none_label_becomes_empty(self):
        d = make_hline_drawing("AMD", 1.0, label=None)  # type: ignore[arg-type]
        assert d.label == ""


# ---------------------------------------------------------------
# Drawing.to_dict / from_dict
# ---------------------------------------------------------------

class TestRoundTrip:
    def test_to_from_dict_is_fixed_point(self):
        d = make_hline_drawing(
            "AMD", 92.5, color="#FF0000", width=2.0,
            style="dashed", label="stop",
        )
        assert Drawing.from_dict(d.to_dict()) == d

    def test_to_dict_keys(self):
        d = make_hline_drawing("AMD", 1.0)
        payload = d.to_dict()
        assert set(payload.keys()) == {
            "kind", "id", "ticker", "price", "color", "width",
            "style", "label", "created_at", "extra",
        }

    def test_from_dict_missing_keys_applies_defaults(self):
        d = Drawing.from_dict({"kind": "hline", "id": "x", "ticker": "amd",
                               "price": 1.0})
        assert d.color == DEFAULT_COLOR
        assert d.width == DEFAULT_WIDTH
        assert d.style == DEFAULT_STYLE
        assert d.label == ""
        assert d.created_at == ""
        assert d.extra == {}
        assert d.ticker == "AMD"  # normalized

    def test_from_dict_invalid_style_falls_back(self):
        d = Drawing.from_dict({"kind": "hline", "id": "x", "ticker": "AMD",
                               "price": 1.0, "style": "wavy"})
        assert d.style == DEFAULT_STYLE

    def test_from_dict_invalid_width_falls_back(self):
        d = Drawing.from_dict({"kind": "hline", "id": "x", "ticker": "AMD",
                               "price": 1.0, "width": "oops"})
        assert d.width == DEFAULT_WIDTH

    def test_from_dict_extra_passes_through(self):
        d = Drawing.from_dict({"kind": "hline", "id": "x", "ticker": "AMD",
                               "price": 1.0, "extra": {"future": "field"}})
        assert d.extra == {"future": "field"}

    def test_to_dict_extra_is_a_copy(self):
        # Mutating the dict from to_dict() must not mutate the
        # underlying drawing's extra.
        d = make_hline_drawing("AMD", 1.0)
        out = d.to_dict()
        out["extra"]["x"] = 1
        assert d.extra == {}


# ---------------------------------------------------------------
# Drawing.replace
# ---------------------------------------------------------------

class TestReplace:
    def test_basic(self):
        d = make_hline_drawing("AMD", 92.5)
        nd = d.replace(price=100.0)
        assert nd.price == 100.0
        # Original unchanged.
        assert d.price == 92.5
        # Id preserved.
        assert nd.id == d.id

    def test_unknown_keys_silently_dropped(self):
        d = make_hline_drawing("AMD", 1.0)
        nd = d.replace(price=2.0, bogus_field="ignored")
        assert nd.price == 2.0
        assert not hasattr(nd, "bogus_field")

    def test_ticker_normalized(self):
        d = make_hline_drawing("AMD", 1.0)
        nd = d.replace(ticker=" msft ")
        assert nd.ticker == "MSFT"

    def test_style_case_insensitive(self):
        d = make_hline_drawing("AMD", 1.0)
        assert d.replace(style="DASHED").style == "dashed"

    def test_style_dashdot_accepted(self):
        # Audit ``drawing-style-options``: ``dashdot`` joined the
        # canonical style set so users have a markedly-distinct
        # alternative at low widths. Coercion must accept it
        # (factory + replace), and mixed case must lowercase.
        d = make_hline_drawing("AMD", 1.0, style="dashdot")
        assert d.style == "dashdot"
        assert d.replace(style="DASHDOT").style == "dashdot"

    def test_style_invalid_falls_back(self):
        d = make_hline_drawing("AMD", 1.0)
        assert d.replace(style="garbage").style == DEFAULT_STYLE

    def test_width_non_positive_falls_back(self):
        d = make_hline_drawing("AMD", 1.0)
        assert d.replace(width=-1).width == DEFAULT_WIDTH
        assert d.replace(width=0).width == DEFAULT_WIDTH

    def test_color_empty_keeps_original(self):
        # Both ``make_hline_drawing`` and ``replace`` lowercase
        # incoming hex (audit ``color-hex-case``); the original
        # ``#ABCDEF`` is stored as ``#abcdef`` and an empty edit
        # preserves that lowercased value.
        d = make_hline_drawing("AMD", 1.0, color="#ABCDEF")
        assert d.color == "#abcdef"
        assert d.replace(color="").color == "#abcdef"
        assert d.replace(color="   ").color == "#abcdef"

    def test_price_non_numeric_keeps_original(self):
        d = make_hline_drawing("AMD", 1.0)
        assert d.replace(price="oops").price == 1.0

    def test_price_nan_keeps_original(self):
        # Audit ``price-coerce-nan-inf``: NaN edits must NOT
        # silently replace a valid price with NaN — matplotlib
        # warn-spams on every redraw and the line vanishes.
        d = make_hline_drawing("AMD", 92.5)
        assert d.replace(price=float("nan")).price == 92.5

    def test_price_inf_keeps_original(self):
        d = make_hline_drawing("AMD", 92.5)
        assert d.replace(price=float("inf")).price == 92.5
        assert d.replace(price=float("-inf")).price == 92.5

    def test_price_nan_string_keeps_original(self):
        d = make_hline_drawing("AMD", 92.5)
        assert d.replace(price="nan").price == 92.5
        assert d.replace(price="inf").price == 92.5
        assert d.replace(price="-inf").price == 92.5

    def test_frozen_dataclass(self):
        d = make_hline_drawing("AMD", 1.0)
        with pytest.raises(Exception):
            d.price = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------
# _coerce_price (audit ``price-coerce-nan-inf``)
# ---------------------------------------------------------------

class TestCoercePrice:
    def test_finite_passes_through(self):
        from tradinglab.drawings.model import _coerce_price

        assert _coerce_price(1.0) == 1.0
        assert _coerce_price(0.0) == 0.0
        assert _coerce_price(-1.5) == -1.5
        assert _coerce_price(100) == 100.0

    def test_string_numeric_parses(self):
        from tradinglab.drawings.model import _coerce_price

        assert _coerce_price("1.5") == 1.5
        assert _coerce_price("  2.0  ") == 2.0

    def test_nan_falls_back_to_default_zero(self):
        from tradinglab.drawings.model import _coerce_price

        assert _coerce_price(float("nan")) == 0.0

    def test_inf_falls_back_to_default_zero(self):
        from tradinglab.drawings.model import _coerce_price

        assert _coerce_price(float("inf")) == 0.0
        assert _coerce_price(float("-inf")) == 0.0

    def test_nan_string_falls_back_to_default_zero(self):
        from tradinglab.drawings.model import _coerce_price

        # `float("nan")` succeeds, returning NaN → must be rejected.
        assert _coerce_price("nan") == 0.0
        assert _coerce_price("NaN") == 0.0
        assert _coerce_price("inf") == 0.0
        assert _coerce_price("Infinity") == 0.0
        assert _coerce_price("-inf") == 0.0

    def test_non_numeric_falls_back_to_default(self):
        from tradinglab.drawings.model import _coerce_price

        assert _coerce_price("oops") == 0.0
        assert _coerce_price(None) == 0.0
        assert _coerce_price(object()) == 0.0

    def test_custom_fallback_for_invalid(self):
        from tradinglab.drawings.model import _coerce_price

        assert _coerce_price(float("nan"), fallback=99.0) == 99.0
        assert _coerce_price("inf", fallback=42.0) == 42.0
        assert _coerce_price(None, fallback=7.5) == 7.5
        # Valid inputs ignore the fallback.
        assert _coerce_price(1.0, fallback=99.0) == 1.0


# ---------------------------------------------------------------
# make_hline_drawing NaN/Inf rejection
# ---------------------------------------------------------------

class TestMakeHlineNanInfRejection:
    """Audit ``price-coerce-nan-inf`` factory-path rejection."""

    def test_nan_collapses_to_zero(self):
        d = make_hline_drawing("AMD", float("nan"))
        assert d.price == 0.0
        import math as _m

        assert not _m.isnan(d.price)

    def test_inf_collapses_to_zero(self):
        assert make_hline_drawing("AMD", float("inf")).price == 0.0
        assert make_hline_drawing("AMD", float("-inf")).price == 0.0


# ---------------------------------------------------------------
# from_dict load-path NaN/Inf rejection
# ---------------------------------------------------------------

class TestFromDictNanInfRejection:
    """Persistence-layer hand-edited JSON must not be able to inject
    NaN/Inf prices into a Drawing.
    """

    def test_payload_with_nan_price_falls_back(self):
        # Note: standard JSON doesn't represent NaN, but Python's
        # ``json.loads`` accepts the non-standard ``NaN`` token by
        # default. Belt-and-braces guard.
        d = Drawing.from_dict({
            "kind": "hline", "id": "x", "ticker": "AMD",
            "price": float("nan"),
        })
        import math as _m

        assert not _m.isnan(d.price)
        assert d.price == 0.0

    def test_payload_with_inf_price_falls_back(self):
        d = Drawing.from_dict({
            "kind": "hline", "id": "x", "ticker": "AMD",
            "price": float("inf"),
        })
        import math as _m

        assert not _m.isinf(d.price)
        assert d.price == 0.0


# ---------------------------------------------------------------
# _coerce_id (audit ``drawing-empty-id``)
# ---------------------------------------------------------------

class TestCoerceId:
    def test_non_empty_string_passes_through(self):
        from tradinglab.drawings.model import _coerce_id

        assert _coerce_id("abc") == "abc"
        assert _coerce_id("manual-id-7") == "manual-id-7"

    def test_strips_whitespace(self):
        from tradinglab.drawings.model import _coerce_id

        assert _coerce_id("  abc  ") == "abc"

    def test_empty_string_generates_uuid(self):
        from tradinglab.drawings.model import _coerce_id

        result = _coerce_id("")
        assert _UUID_HEX_RE.match(result), f"not uuid hex: {result!r}"

    def test_whitespace_only_generates_uuid(self):
        from tradinglab.drawings.model import _coerce_id

        result = _coerce_id("   \t\n  ")
        assert _UUID_HEX_RE.match(result), f"not uuid hex: {result!r}"

    def test_none_generates_uuid(self):
        from tradinglab.drawings.model import _coerce_id

        result = _coerce_id(None)
        assert _UUID_HEX_RE.match(result), f"not uuid hex: {result!r}"

    def test_fallback_used_for_empty(self):
        from tradinglab.drawings.model import _coerce_id

        # When fallback is provided AND input is empty,
        # the fallback wins over generating a new UUID.
        assert _coerce_id("", fallback="keep-this") == "keep-this"
        assert _coerce_id("   ", fallback="keep-this") == "keep-this"
        assert _coerce_id(None, fallback="keep-this") == "keep-this"

    def test_valid_input_ignores_fallback(self):
        from tradinglab.drawings.model import _coerce_id

        assert _coerce_id("real-id", fallback="ignored") == "real-id"

    def test_uniqueness_of_generated_ids(self):
        from tradinglab.drawings.model import _coerce_id

        ids = {_coerce_id("") for _ in range(50)}
        assert len(ids) == 50


# ---------------------------------------------------------------
# _coerce_width upper-bound (audit ``drawing-width-upper-bound``)
# ---------------------------------------------------------------

class TestCoerceWidthUpperBound:
    def test_at_max_passes_through(self):
        from tradinglab.drawings.model import _coerce_width

        assert _coerce_width(MAX_WIDTH) == MAX_WIDTH

    def test_just_below_max_passes_through(self):
        from tradinglab.drawings.model import _coerce_width

        assert _coerce_width(MAX_WIDTH - 0.1) == pytest.approx(MAX_WIDTH - 0.1)

    def test_just_above_max_clamps_to_max(self):
        from tradinglab.drawings.model import _coerce_width

        assert _coerce_width(MAX_WIDTH + 0.1) == MAX_WIDTH

    def test_pathological_large_clamps_to_max(self):
        from tradinglab.drawings.model import _coerce_width

        assert _coerce_width(50.0) == MAX_WIDTH
        assert _coerce_width(1000.0) == MAX_WIDTH
        assert _coerce_width(1e9) == MAX_WIDTH

    def test_string_too_large_clamps_to_max(self):
        from tradinglab.drawings.model import _coerce_width

        assert _coerce_width("50") == MAX_WIDTH

    def test_nan_falls_back_to_default(self):
        # Pre-fix the ``v <= 0`` guard returned False for NaN
        # (NaN compares False to everything), letting NaN escape.
        from tradinglab.drawings.model import _coerce_width

        assert _coerce_width(float("nan")) == DEFAULT_WIDTH

    def test_inf_falls_back_to_default(self):
        # ``float("inf") <= 0`` is False, so without an explicit
        # guard Inf would pass through.
        from tradinglab.drawings.model import _coerce_width

        assert _coerce_width(float("inf")) == DEFAULT_WIDTH
        assert _coerce_width(float("-inf")) == DEFAULT_WIDTH


# ---------------------------------------------------------------
# snap_price_to_grid (audit ``drawings-snap-instrument``)
# ---------------------------------------------------------------

class TestSnapPriceMagnitudeFallback:
    """Behaviour when ``visible_range`` is ``None`` — the
    magnitude-based fallback used when the caller has no axes
    handle (or it has degenerate ``get_ylim()``)."""

    def test_stock_prices_snap_to_cents(self):
        # The bulk-of-US-equities path: >=$1 → 2 decimal places.
        assert snap_price_to_grid(185.5234) == 185.52
        assert snap_price_to_grid(1.0) == 1.0
        assert snap_price_to_grid(999999.999) == round(999999.999, 2)

    def test_one_dollar_boundary_is_cents(self):
        # Exactly $1.00 falls in the ">=1" branch.
        assert snap_price_to_grid(1.005) == round(1.005, 2)

    def test_sub_dollar_keeps_significant_figures(self):
        # 0.5 → decade=-1 → ndigits=4 → grid 0.0001.
        assert snap_price_to_grid(0.5234) == round(0.5234, 4)
        assert snap_price_to_grid(0.123456) == round(0.123456, 4)

    def test_low_crypto_keeps_more_decimals(self):
        # 0.0042 → decade=-3 → ndigits=6.
        assert snap_price_to_grid(0.00421234) == round(0.00421234, 6)

    def test_very_low_crypto_clamps_at_10_decimals(self):
        # decade=-12 → ndigits clamped to 10.
        v = 1.23456789e-12
        assert snap_price_to_grid(v) == round(v, 10)

    def test_negative_prices_handled_by_magnitude(self):
        # Spec says prices are always positive but defensively
        # we mirror the magnitude logic so a stray negative
        # doesn't blow up to ndigits=0.
        assert snap_price_to_grid(-185.5234) == -185.52
        assert snap_price_to_grid(-0.5234) == round(-0.5234, 4)

    def test_zero_unchanged(self):
        assert snap_price_to_grid(0.0) == 0.0

    def test_nan_unchanged(self):
        assert math.isnan(snap_price_to_grid(float("nan")))

    def test_inf_unchanged(self):
        # snap_price_to_grid passes non-finite through so
        # downstream coerce helpers can reject them at the
        # ``Drawing(...)`` construction boundary.
        assert math.isinf(snap_price_to_grid(float("inf")))
        assert math.isinf(snap_price_to_grid(float("-inf")))


class TestSnapPriceAxesAware:
    """When ``visible_range`` is provided, the snap follows the
    visible scale of the chart rather than the absolute price
    magnitude. This is the path the chart-canvas callers use."""

    def test_stock_chart_span_snaps_to_cents(self):
        # A typical stock chart visible window: $180 → $200.
        # span = 20 → target = 0.01 → grid = 0.01 → 2 dp.
        snapped = snap_price_to_grid(185.5234, visible_range=20.0)
        assert snapped == 185.52

    def test_forex_chart_snaps_to_pipettes(self):
        # EUR/USD style: 1.080 → 1.090, span=0.01.
        # target = 5e-6 → grid = 1e-6 → 6 dp.
        snapped = snap_price_to_grid(1.085234, visible_range=0.01)
        assert snapped == round(1.085234, 6)

    def test_crypto_chart_snaps_finely(self):
        # 0.001 → 0.005 visible window, span=0.004.
        # target = 2e-6 → grid = 1e-6 → 6 dp.
        snapped = snap_price_to_grid(0.00421234, visible_range=0.004)
        assert snapped == round(0.00421234, 6)

    def test_btc_chart_snaps_to_dollar(self):
        # 60000 → 65000 visible window, span=5000.
        # target = 2.5 → grid = 1.0 → snap to whole dollars.
        snapped = snap_price_to_grid(61234.56, visible_range=5000.0)
        # round to nearest dollar.
        assert snapped == 61235.0

    def test_axes_aware_falls_back_when_visible_range_zero(self):
        # Degenerate axes (lo==hi) → fall back to magnitude path.
        assert snap_price_to_grid(185.5234, visible_range=0.0) == 185.52

    def test_axes_aware_falls_back_when_visible_range_negative(self):
        # Inverted axes shouldn't happen but defend anyway.
        assert snap_price_to_grid(185.5234, visible_range=-1.0) == 185.52

    def test_axes_aware_falls_back_when_visible_range_nan(self):
        # NaN visible_range → fall back to magnitude path.
        assert snap_price_to_grid(185.5234, visible_range=float("nan")) == 185.52

    def test_axes_aware_falls_back_when_visible_range_inf(self):
        # Inf visible_range → fall back to magnitude path
        # (infinite span shouldn't pick a meaningful grid).
        assert snap_price_to_grid(185.5234, visible_range=float("inf")) == 185.52

    def test_snap_is_idempotent(self):
        # Snapping an already-snapped value returns it unchanged.
        snapped = snap_price_to_grid(185.5234, visible_range=20.0)
        again = snap_price_to_grid(snapped, visible_range=20.0)
        assert snapped == again

    def test_snap_preserves_finite_invariant(self):
        # Output is always finite when input is finite (verified
        # by the make_hline_drawing factory's _coerce_price guard).
        for price, vrange in [
            (185.52, 20.0),
            (0.0001, 0.001),
            (50000.0, 1000.0),
            (-185.52, 20.0),
        ]:
            out = snap_price_to_grid(price, visible_range=vrange)
            assert math.isfinite(out)

    def test_grid_is_floor_power_of_10(self):
        # target = span/2000, grid = 10^floor(log10(target)).
        # span=100 → target=0.05 → log10≈-1.30 → floor=-2 → grid=0.01.
        snapped = snap_price_to_grid(123.456, visible_range=100.0)
        assert snapped == 123.46
        # span=1 → target=5e-4 → log10≈-3.30 → floor=-4 → grid=1e-4.
        snapped = snap_price_to_grid(1.23456, visible_range=1.0)
        assert snapped == round(1.23456, 4)


class TestSnapPriceMakeHlineIntegration:
    """End-to-end: caller snaps with this helper, factory wraps
    the result. The two layers must agree on `math.isfinite` so
    a snapped value is never rejected by ``_coerce_price``."""

    def test_factory_accepts_snapped_stock_price(self):
        snapped = snap_price_to_grid(185.5234, visible_range=20.0)
        d = make_hline_drawing("AAPL", snapped)
        assert d.price == 185.52

    def test_factory_accepts_snapped_forex_price(self):
        snapped = snap_price_to_grid(1.085234, visible_range=0.01)
        d = make_hline_drawing("EURUSD", snapped)
        assert d.price == round(1.085234, 6)

    def test_factory_accepts_snapped_low_crypto_price(self):
        snapped = snap_price_to_grid(0.00421234, visible_range=0.004)
        d = make_hline_drawing("SHIB", snapped)
        assert d.price == round(0.00421234, 6)

    def test_max_width_constant_is_sane(self):
        # The dialog slider caps at 5.0; MAX_WIDTH must leave
        # legitimate headroom but stay below "obscures the chart".
        assert MAX_WIDTH >= 5.0
        assert MAX_WIDTH < 50.0


# ---------------------------------------------------------------
# make_hline_drawing width clamp
# ---------------------------------------------------------------

class TestMakeHlineWidthClamp:
    def test_huge_width_clamped(self):
        d = make_hline_drawing("AMD", 1.0, width=50.0)
        assert d.width == MAX_WIDTH

    def test_nan_width_falls_back(self):
        d = make_hline_drawing("AMD", 1.0, width=float("nan"))
        assert d.width == DEFAULT_WIDTH

    def test_inf_width_falls_back(self):
        d = make_hline_drawing("AMD", 1.0, width=float("inf"))
        assert d.width == DEFAULT_WIDTH


# ---------------------------------------------------------------
# from_dict width clamp
# ---------------------------------------------------------------

class TestFromDictWidthClamp:
    def test_payload_with_huge_width_clamped(self):
        d = Drawing.from_dict({
            "kind": "hline", "id": "x", "ticker": "AMD",
            "price": 1.0, "width": 50.0,
        })
        assert d.width == MAX_WIDTH

    def test_payload_with_nan_width_falls_back(self):
        d = Drawing.from_dict({
            "kind": "hline", "id": "x", "ticker": "AMD",
            "price": 1.0, "width": float("nan"),
        })
        assert d.width == DEFAULT_WIDTH


# ---------------------------------------------------------------
# replace width clamp
# ---------------------------------------------------------------

class TestReplaceWidthClamp:
    def test_replace_huge_width_clamps(self):
        d = make_hline_drawing("AMD", 1.0, width=2.0)
        assert d.replace(width=50.0).width == MAX_WIDTH

    def test_replace_nan_width_falls_back(self):
        d = make_hline_drawing("AMD", 1.0, width=2.0)
        # Falls back to DEFAULT_WIDTH (same as the load path).
        assert d.replace(width=float("nan")).width == DEFAULT_WIDTH


# ---------------------------------------------------------------
# make_hline_drawing empty-id rejection
# ---------------------------------------------------------------

class TestMakeHlineEmptyIdRejection:
    """Audit ``drawing-empty-id`` factory-path guard."""

    def test_whitespace_only_id_generates_uuid(self):
        d = make_hline_drawing("AMD", 1.0, drawing_id="   ")
        assert _UUID_HEX_RE.match(d.id), f"id not uuid hex: {d.id!r}"

    def test_id_with_surrounding_whitespace_stripped(self):
        d = make_hline_drawing("AMD", 1.0, drawing_id="  real-id  ")
        assert d.id == "real-id"

    def test_explicit_empty_string_id_generates_uuid(self):
        d = make_hline_drawing("AMD", 1.0, drawing_id="")
        assert _UUID_HEX_RE.match(d.id), f"id not uuid hex: {d.id!r}"


# ---------------------------------------------------------------
# from_dict empty-id rejection
# ---------------------------------------------------------------

class TestFromDictEmptyIdRejection:
    """A hand-edited or corrupt drawings.json must not load a
    drawing with id `""`. Store lookups would collide on every
    operation.
    """

    def test_missing_id_field_generates_uuid(self):
        d = Drawing.from_dict({"kind": "hline", "ticker": "AMD", "price": 1.0})
        assert _UUID_HEX_RE.match(d.id), f"id not uuid hex: {d.id!r}"

    def test_empty_id_field_generates_uuid(self):
        d = Drawing.from_dict({"kind": "hline", "id": "", "ticker": "AMD", "price": 1.0})
        assert _UUID_HEX_RE.match(d.id), f"id not uuid hex: {d.id!r}"

    def test_whitespace_id_field_generates_uuid(self):
        d = Drawing.from_dict({"kind": "hline", "id": "  \t ", "ticker": "AMD", "price": 1.0})
        assert _UUID_HEX_RE.match(d.id), f"id not uuid hex: {d.id!r}"

    def test_valid_id_preserved(self):
        d = Drawing.from_dict({"kind": "hline", "id": "stable-id-7", "ticker": "AMD", "price": 1.0})
        assert d.id == "stable-id-7"


# ---------------------------------------------------------------
# replace empty-id guard
# ---------------------------------------------------------------

class TestReplaceEmptyIdGuard:
    def test_replace_with_empty_id_keeps_original(self):
        d = make_hline_drawing("AMD", 1.0, drawing_id="abc123")
        assert d.replace(id="").id == "abc123"

    def test_replace_with_whitespace_id_keeps_original(self):
        d = make_hline_drawing("AMD", 1.0, drawing_id="abc123")
        assert d.replace(id="   ").id == "abc123"

    def test_replace_with_valid_id_applies(self):
        d = make_hline_drawing("AMD", 1.0, drawing_id="abc123")
        assert d.replace(id="new-id").id == "new-id"

    def test_replace_strips_whitespace_around_new_id(self):
        d = make_hline_drawing("AMD", 1.0, drawing_id="abc123")
        assert d.replace(id="  new-id  ").id == "new-id"


# ---------------------------------------------------------------
# constants
# ---------------------------------------------------------------

class TestConstants:
    def test_default_color_is_tradingview_blue(self):
        # The locked decision per plan.md: TradingView blue, fixed.
        # Changing this constant requires user discussion.
        assert DEFAULT_COLOR == "#2962ff"

    def test_default_width_is_one(self):
        assert DEFAULT_WIDTH == 1.0

    def test_default_style_in_valid_set(self):
        assert DEFAULT_STYLE in VALID_STYLES

    def test_valid_styles_complete(self):
        # ``dashdot`` joined the set in audit
        # ``drawing-style-options`` so users always have a
        # markedly-distinct alternative at small widths.
        assert VALID_STYLES == {"solid", "dashed", "dotted", "dashdot"}

    def test_allowed_kinds_v1(self):
        # v1 only ships hlines. Future additions must update both
        # this assertion and the spec.
        assert M.ALLOWED_KINDS == {"hline"}
