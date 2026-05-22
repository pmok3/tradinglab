"""Corporate-action records produced by the sandbox engine.

When a sandbox session holds an open long position across an
ex-dividend, stock-split, reverse-split, or spin-off date, the engine
applies a deterministic adjustment to the portfolio:

* **Cash dividends** and **special / one-off dividends** credit
  ``amount * quantity`` to :class:`Portfolio.cash`.
* **Stock splits** (and reverse splits) scale the held quantity by
  ``ratio_num / ratio_den``. Fractional shares are tolerated end-to-end;
  rounding is the consumer's call.
* **Spin-offs** credit the cash equivalent of the spun-off value to
  :class:`Portfolio.cash`. (We don't materialise the child position in
  v1 — that would require a parallel `BarSeries` for the child ticker,
  which the user can register manually via ``register_ticker`` if they
  want to trade the child.)

These records are **engine-output facts**, not ambient event data.
They are persisted on :class:`SessionResult` alongside :class:`Fill`
so a loaded session can reproduce the held-through-ex-div equity
curve byte-for-byte, even if the upstream event provider has since
revised the historical record.

The raw :class:`EarningsRecord` / :class:`DividendRecord` data
(:mod:`tradinglab.events`) is **ambient context** — it stays out
of :class:`SessionResult`. See ``backtest/engine.spec.md`` and
``events/__init__.py`` for the full design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorporateAction:
    """Input record: a corporate action scheduled to be applied to the
    portfolio at a future ex-event timestamp.

    The :class:`SandboxController` converts each per-symbol
    :class:`~tradinglab.events.DividendRecord` into a
    :class:`CorporateAction` at session start and registers them with
    the engine. The engine drains the queue during the corporate-action
    tick phase, applies the per-share cash credit or quantity rescale
    to the portfolio, and emits a :class:`CashAdjustment` or
    :class:`QuantityAdjustment` *output* record for the
    :class:`SessionResult`.

    Keeping this input type in :mod:`tradinglab.backtest` (not
    :mod:`tradinglab.events`) preserves the engine's headless
    contract — the backtest package never imports from ``events``.

    Kinds:
      * ``"cash_dividend"``      — credit ``amount * quantity`` to cash.
      * ``"special_dividend"``   — same as cash_dividend but flagged.
      * ``"spinoff_cash"``       — same; v1 collapses spinoffs to cash.
      * ``"stock_split"``        — rescale quantity by ratio_num/ratio_den.
                                    Rescale avg_cost by the inverse so the
                                    position's cost basis is preserved.
    """
    ts: int
    kind: str
    amount: float = 0.0
    ratio_num: int = 1
    ratio_den: int = 1
    source_ref: str = ""


@dataclass(frozen=True)
class CashAdjustment:
    """A cash credit applied to the portfolio at an ex-event timestamp.

    ``amount_per_share`` is the per-share cash flow at face value (e.g.
    ``0.485`` for a quarterly KO dividend). ``quantity`` is the position
    size at the moment of application — captured here for the audit
    trail; the engine has already debited / credited ``Portfolio.cash``
    by ``amount_per_share * quantity`` before this record is emitted.

    ``reason`` is one of ``"cash_dividend"``, ``"special_dividend"``,
    ``"spinoff_cash"``. The renderer / performance view can group by
    reason without re-querying the event provider.

    ``source_ref`` is a free-form provenance string ("yfinance" plus
    optionally a quarter / declaration identifier). It does NOT
    participate in equity-curve math — purely for forensic display.
    """
    ts: int
    symbol: str
    amount_per_share: float
    quantity: float
    reason: str
    source_ref: str = ""


@dataclass(frozen=True)
class QuantityAdjustment:
    """A share-quantity rescaling applied to the portfolio at an
    ex-split timestamp.

    Held quantity is multiplied by ``ratio_num / ratio_den``. For a
    2:1 forward split set ``ratio_num=2, ratio_den=1`` (quantity
    doubles). For a 1:10 reverse split set ``ratio_num=1,
    ratio_den=10`` (quantity divides by ten — caller decides whether
    to round to whole shares).

    ``pre_quantity`` is the held quantity *before* the adjustment,
    captured for the audit trail.

    Note: yfinance returns adjusted bars, so the price axis already
    reflects the post-split scale. The quantity adjustment exists so
    that ``equity = cash + quantity * price`` continues to make sense
    across the split boundary — i.e. so that mark-to-market on the
    adjusted price uses the adjusted quantity, not the pre-split one.
    """
    ts: int
    symbol: str
    ratio_num: int
    ratio_den: int
    pre_quantity: float
    reason: str = "stock_split"
    source_ref: str = ""


__all__ = ("CorporateAction", "CashAdjustment", "QuantityAdjustment")
