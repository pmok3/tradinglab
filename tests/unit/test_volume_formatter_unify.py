"""Pin volume / revenue formatters to ``.2f`` precision at all magnitudes.

Audit finding ``volume-formatter-unify`` (1 of 75): the share-volume
formatter (``tradinglab.formatting.fmt_volume``) used ``.2f`` for B and
M but ``.1f`` for K. A parallel earnings-revenue formatter
(``tradinglab.events.render._format_revenue``) used ``.2f`` only for
the B branch and ``.1f`` for both M and K. Effect: a stock whose
average volume rolled from 999_500 → 1_005_000 would jump from
``"999.5K"`` (3 sig figs displayed) to ``"1.00M"`` (3 sig figs
displayed) but with a sudden change in **decimal-place count** — a
visual "snap" that no other axis label suffers from.

Convention picked: ``.2f`` precision at every magnitude tier (B / M /
K). Sub-K still uses ``.0f`` because fractional shares are not a
thing.
"""

from __future__ import annotations

import math

from tradinglab.events.render import _format_revenue
from tradinglab.formatting import fmt_volume

# ----- fmt_volume ---------------------------------------------------------


def test_fmt_volume_billions_uses_two_decimals() -> None:
    assert fmt_volume(1_234_567_890) == "1.23B"
    assert fmt_volume(12_000_000_000) == "12.00B"


def test_fmt_volume_millions_uses_two_decimals() -> None:
    assert fmt_volume(1_234_567) == "1.23M"
    assert fmt_volume(12_000_000) == "12.00M"


def test_fmt_volume_thousands_uses_two_decimals() -> None:
    # The critical regression: the K branch used to be .1f, giving
    # "999.5K". After the fix it must be .2f.
    assert fmt_volume(999_500) == "999.50K"
    assert fmt_volume(1_234) == "1.23K"
    assert fmt_volume(10_000) == "10.00K"


def test_fmt_volume_sub_thousand_uses_zero_decimals() -> None:
    assert fmt_volume(0) == "0"
    assert fmt_volume(42) == "42"
    assert fmt_volume(999) == "999"


def test_fmt_volume_boundaries_dont_double_count() -> None:
    # Exactly at each boundary should pick the higher tier (uses
    # ``>=`` comparison).
    assert fmt_volume(1_000) == "1.00K"
    assert fmt_volume(1_000_000) == "1.00M"
    assert fmt_volume(1_000_000_000) == "1.00B"


# ----- _format_revenue ----------------------------------------------------


def test_format_revenue_billions_uses_two_decimals() -> None:
    assert _format_revenue(1.234e9) == "$1.23B"
    assert _format_revenue(12.0e9) == "$12.00B"


def test_format_revenue_millions_uses_two_decimals() -> None:
    # Used to be .1f -> "$123.5M". Now .2f.
    assert _format_revenue(123.5e6) == "$123.50M"
    assert _format_revenue(1.234e6) == "$1.23M"


def test_format_revenue_thousands_uses_two_decimals() -> None:
    # Used to be .1f -> "$999.5K". Now .2f.
    assert _format_revenue(999.5e3) == "$999.50K"
    assert _format_revenue(1.234e3) == "$1.23K"


def test_format_revenue_sub_thousand_uses_zero_decimals() -> None:
    assert _format_revenue(0.0) == "$0"
    assert _format_revenue(999.4) == "$999"


def test_format_revenue_negative_values_pick_correct_tier() -> None:
    # The branch logic uses ``abs(value) >= …`` so negatives also
    # get the SI suffix at the right magnitude. Sign is preserved.
    assert _format_revenue(-1.5e6) == "$-1.50M"
    assert _format_revenue(-2.5e3) == "$-2.50K"


def test_format_revenue_nan_returns_dash() -> None:
    assert _format_revenue(math.nan) == "—"
