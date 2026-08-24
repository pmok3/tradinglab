"""Curated catalog of market-internals rows shown in the **Quant** side tab.

The Quant tab is a launcher, not an analytics engine: every row names a
quantity that says something about the *state of the market* rather than
about one company, and double-clicking it loads that quantity onto the
chart exactly like a watchlist ticker. The catalog is therefore just data
— symbol, display name, and a one-line "what this tells you".

Three kinds of row exist:

- **Plain tickers** (``SPY``, ``HYG``) and **index shorthand** (``VIX``,
  ``TNX``). Shorthand is resolved per-source by ``data/index_aliases.py``,
  so a row is written once and works on every vendor.
- **Ratios**, which the chart already understands (AGENTS.md §7.37). A
  *quotient* (``RSP/SPY``) is a relative-strength read; a *scaled symbol*
  (``VIX/15.87``) is an exact rescale of one series.
- **Unavailable rows** (``GEX``, ``DIX``), which carry no symbol. They are
  listed because they belong in a market-internals panel, and rendered
  disabled so the gap is visible rather than silently absent.

The expected-move rows deserve a note, because the constants are not
arbitrary. Implied volatility is quoted annualised, so dividing by the
square root of the number of periods in a year converts it to a one-sigma
move over one period: ``sqrt(252) = 15.87`` trading days, ``sqrt(52) =
7.21`` weeks, ``sqrt(12) = 3.46`` months. ``VIX/15.87`` is therefore
literally "the percent move SPY is priced to make tomorrow, one standard
deviation". Because the divisor is a positive constant the result is an
*exact* rescale — highs stay highs and no bar is dropped, unlike a
quotient (§7.37).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

#: Shown in the Symbol column and in the double-click status message for a
#: row with no data source. Kept as one string so the tab, the status bar,
#: and the tests can't drift apart.
UNAVAILABLE_SYMBOL_TEXT = "—"


@dataclass(frozen=True)
class QuantRow:
    """One row in the Quant tab.

    ``symbol`` is empty exactly when ``available`` is ``False``; that pairing
    is what the tab keys its disabled rendering off, and it is asserted by
    :mod:`tests.unit.quant.test_catalog`.
    """

    key: str
    name: str
    symbol: str
    description: str
    available: bool = True
    unavailable_reason: str = ""


@dataclass(frozen=True)
class QuantGroup:
    """A named, collapsible section of the Quant tab."""

    key: str
    name: str
    rows: tuple[QuantRow, ...] = field(default_factory=tuple)


_NO_FEED = (
    "No data source is wired for this quantity yet — it is not quotable "
    "as a ticker by any configured vendor."
)


QUANT_CATALOG: tuple[QuantGroup, ...] = (
    QuantGroup(
        key="volatility",
        name="Volatility & expected move",
        rows=(
            QuantRow(
                key="vix", name="VIX", symbol="VIX",
                description=(
                    "30-day implied volatility on the S&P 500 — the fear gauge."
                ),
            ),
            QuantRow(
                key="spy_em_1d", name="SPY expected move (1d)",
                symbol="VIX/15.87",
                description=(
                    "VIX ÷ √252. One-sigma move SPY is priced to make over the "
                    "next session, in percent."
                ),
            ),
            QuantRow(
                key="spy_em_1w", name="SPY expected move (1w)",
                symbol="VIX/7.21",
                description="VIX ÷ √52. Same idea over the next week.",
            ),
            QuantRow(
                key="spy_em_1m", name="SPY expected move (1m)",
                symbol="VIX/3.46",
                description="VIX ÷ √12. Same idea over the next month.",
            ),
            QuantRow(
                key="qqq_em_1d", name="QQQ expected move (1d)",
                symbol="VXN/15.87",
                description=(
                    "Nasdaq-100 implied volatility ÷ √252 — the QQQ counterpart "
                    "of the SPY daily expected move."
                ),
            ),
            QuantRow(
                key="vvix", name="VVIX", symbol="VVIX",
                description=(
                    "Implied volatility OF the VIX. Bids up as hedging demand "
                    "builds, often before VIX itself moves."
                ),
            ),
            QuantRow(
                key="skew", name="SKEW", symbol="^SKEW",
                description=(
                    "Relative price of far out-of-the-money S&P puts — how "
                    "expensive crash insurance has become."
                ),
            ),
            QuantRow(
                key="move", name="MOVE", symbol="^MOVE",
                description=(
                    "Implied volatility on US Treasuries. Rate stress usually "
                    "leads equity stress. Written with its caret because bare "
                    "MOVE is a listed equity."
                ),
            ),
            QuantRow(
                key="gvz", name="Gold volatility", symbol="^GVZ",
                description="Implied volatility on GLD — the metal's fear gauge.",
            ),
            QuantRow(
                key="ovx", name="Oil volatility", symbol="^OVX",
                description=(
                    "Implied volatility on USO. Spikes on supply shocks and "
                    "geopolitical risk."
                ),
            ),
        ),
    ),
    QuantGroup(
        key="credit",
        name="Credit & risk appetite",
        rows=(
            QuantRow(
                key="hyg", name="HYG (high-yield bonds)", symbol="HYG",
                description=(
                    "High-yield corporate bond ETF — credit's own risk asset."
                ),
            ),
            QuantRow(
                key="tlt", name="TLT (treasury bonds)", symbol="TLT",
                description=(
                    "20-year-plus Treasury ETF — duration and the "
                    "flight-to-quality bid."
                ),
            ),
            QuantRow(
                key="credit_stress", name="Credit stress", symbol="HYG/LQD",
                description=(
                    "Junk versus investment grade. Rolls over first when credit "
                    "cracks, often ahead of equities."
                ),
            ),
            QuantRow(
                key="credit_risk_appetite", name="Risk appetite (credit)",
                symbol="HYG/TLT",
                description=(
                    "Credit versus duration. Rising is risk-on; falling is a "
                    "flight to quality."
                ),
            ),
        ),
    ),
    QuantGroup(
        key="rates",
        name="Rates & the curve",
        rows=(
            QuantRow(
                key="tnx", name="10-year yield", symbol="TNX",
                description=(
                    "US 10-year Treasury yield (quoted ×10) — the discount rate "
                    "everything else is priced against."
                ),
            ),
            QuantRow(
                key="curve_slope", name="Curve slope", symbol="TNX/IRX",
                description=(
                    "10-year ÷ 13-week yield. Below 1.0 the curve is inverted."
                ),
            ),
            QuantRow(
                key="duration_pref", name="Duration preference", symbol="TLT/IEI",
                description=(
                    "Long versus intermediate Treasuries. Rising means the long "
                    "end is being bid."
                ),
            ),
        ),
    ),
    QuantGroup(
        key="breadth",
        name="Breadth",
        rows=(
            QuantRow(
                key="rsp", name="RSP (equal-weight S&P)", symbol="RSP",
                description=(
                    "Equal-weight S&P 500 — the average stock rather than the "
                    "biggest ones."
                ),
            ),
            QuantRow(
                key="breadth_rsp_spy", name="Breadth (equal vs cap)",
                symbol="RSP/SPY",
                description=(
                    "Equal-weight ÷ cap-weight. Falling means leadership is "
                    "narrowing into megacaps."
                ),
            ),
            QuantRow(
                key="small_vs_large", name="Small vs large", symbol="IWM/SPY",
                description=(
                    "Russell 2000 versus the S&P 500. Small caps lead early "
                    "cycle and break first late."
                ),
            ),
            QuantRow(
                key="megacap_tilt", name="Megacap tilt", symbol="QQQ/SPY",
                description=(
                    "Nasdaq-100 versus the S&P 500. Rising means concentration "
                    "into big tech."
                ),
            ),
        ),
    ),
    QuantGroup(
        key="rotation",
        name="Sector rotation",
        rows=(
            QuantRow(
                key="equity_risk_appetite", name="Risk appetite (equity)",
                symbol="XLY/XLP",
                description=(
                    "Discretionary versus staples — the cleanest intra-equity "
                    "risk-on/risk-off read."
                ),
            ),
            QuantRow(
                key="semis_leadership", name="Semis leadership", symbol="SMH/SPY",
                description=(
                    "Semiconductors versus the index. Semis usually turn before "
                    "the broad tape does."
                ),
            ),
            QuantRow(
                key="defensive_bid", name="Defensive bid", symbol="XLU/SPY",
                description=(
                    "Utilities versus the index. Rising while SPY rises marks a "
                    "nervous rally."
                ),
            ),
            QuantRow(
                key="growth_vs_value", name="Growth vs value", symbol="IWF/IWD",
                description=(
                    "Russell 1000 growth versus value — the factor regime in one "
                    "line."
                ),
            ),
        ),
    ),
    QuantGroup(
        key="cross_asset",
        name="Cross-asset",
        rows=(
            QuantRow(
                key="gld", name="Gold", symbol="GLD",
                description="Gold ETF — real-rate and debasement hedge.",
            ),
            QuantRow(
                key="gold_vs_equity", name="Gold vs equity", symbol="GLD/SPY",
                description=(
                    "Rising means capital prefers hard assets over earnings."
                ),
            ),
            QuantRow(
                key="dollar", name="US dollar", symbol="UUP",
                description=(
                    "Dollar index ETF. A strong dollar tightens global liquidity."
                ),
            ),
            QuantRow(
                key="bitcoin", name="Bitcoin", symbol="BTC-USD",
                description=(
                    "The highest-beta liquidity proxy — often turns ahead of "
                    "equities."
                ),
            ),
        ),
    ),
    QuantGroup(
        key="positioning",
        name="Positioning",
        rows=(
            QuantRow(
                key="gex", name="GEX (gamma exposure)", symbol="",
                description=(
                    "Net dealer gamma. Positive gamma pins price; negative gamma "
                    "amplifies every move."
                ),
                available=False, unavailable_reason=_NO_FEED,
            ),
            QuantRow(
                key="dix", name="DIX (dark pool index)", symbol="",
                description=(
                    "Dark-pool buying as a share of volume. High readings mark "
                    "quiet accumulation."
                ),
                available=False, unavailable_reason=_NO_FEED,
            ),
        ),
    ),
)


def iter_rows(catalog: tuple[QuantGroup, ...] | None = None) -> Iterator[QuantRow]:
    """Yield every :class:`QuantRow` in catalog order, groups flattened."""
    for group in catalog if catalog is not None else QUANT_CATALOG:
        yield from group.rows


def available_rows(
    catalog: tuple[QuantGroup, ...] | None = None,
) -> list[QuantRow]:
    """Return only the rows that name a fetchable symbol."""
    return [r for r in iter_rows(catalog) if r.available]


def available_symbols(
    catalog: tuple[QuantGroup, ...] | None = None,
) -> list[str]:
    """Return each fetchable symbol once, in catalog order.

    This is the seam the Quant tab uses to warm its Last column, and the
    one a future sandbox / export "pre-download quant series" checkbox
    should consume — so the catalog stays the single source of truth for
    what "the quant set" means.
    """
    seen: set[str] = set()
    out: list[str] = []
    for row in available_rows(catalog):
        sym = row.symbol.upper()
        if sym not in seen:
            seen.add(sym)
            out.append(row.symbol)
    return out


def row_for_key(key: str) -> QuantRow | None:
    """Return the row with ``key``, or ``None``."""
    for row in iter_rows():
        if row.key == key:
            return row
    return None


__all__ = [
    "QUANT_CATALOG",
    "UNAVAILABLE_SYMBOL_TEXT",
    "QuantGroup",
    "QuantRow",
    "available_rows",
    "available_symbols",
    "iter_rows",
    "row_for_key",
]
