"""Pre-trade form + post-trade review records."""

from __future__ import annotations

from dataclasses import dataclass

DECISION_ACTIONS: tuple[str, ...] = ("long", "short", "pass", "watch")


@dataclass(frozen=True)
class DecisionRecord:
    """A discretionary recognition decision logged during replay.

    Unlike an order journal entry, a decision may be ``pass`` or
    ``watch`` and has no required relationship to a trade.
    """
    ts: int
    symbol: str
    action: str
    setup_tag: str
    confidence: int
    note: str = ""


@dataclass(frozen=True)
class PreTradeEntry:
    """Immutable record captured at order-submission time."""
    order_id: str
    ts: int
    symbol: str
    side: str            # "buy" | "sell"
    setup_tag: str
    thesis: str
    conviction: int
    size: float
    target: float | None = None
    notes: str = ""
    # Earnings/dividends feature: proximity context captured at
    # submit-order time so the post-session performance analysis can
    # group trades by their event proximity without re-fetching the
    # event provider. All six are additive default-safe so existing
    # save files round-trip unchanged.
    #
    # ``next_earnings_ts``  / ``last_earnings_ts``  — UTC ms-since-epoch
    #   of the nearest forward / past earnings print at submit time,
    #   or 0 when unknown / unavailable. Always 0 in blind mode for
    #   the forward field (per the gating contract).
    # ``last_dividend_ts``  — UTC ms-since-epoch of the most recent
    #   ex-dividend, or 0 when unknown.
    # ``last_split_ts``     — UTC ms-since-epoch of the most recent
    #   stock split, or 0 when unknown.
    # ``earnings_proximity_tag`` — one of "earnings_pre_print",
    #   "earnings_post_print", or "" — set when the submit time falls
    #   within ``earnings_window_days`` of an earnings print.
    # ``dividend_proximity_tag`` — one of "ex_div_day",
    #   "post_special_div", or "" — set when the submit time falls
    #   on or just after a dividend ex-date.
    next_earnings_ts: int = 0
    last_earnings_ts: int = 0
    last_dividend_ts: int = 0
    last_split_ts: int = 0
    earnings_proximity_tag: str = ""
    dividend_proximity_tag: str = ""


@dataclass(frozen=True)
class PostTradeReview:
    """Computed at position-close time.

    ``mae`` / ``mfe`` are dollar-denominated *adverse* / *favourable*
    excursions over the holding period. ``*_pct`` are signed
    percentages of entry price. ``ref_pre_trade_id`` points at the
    ``PreTradeEntry.order_id`` that opened the trade.

    ``user_review`` is filled by the sandbox UI's post-trade modal
    (Phase 1c). The engine emits the record with an empty string;
    the controller calls :func:`dataclasses.replace` to attach the
    user's text once the modal returns. Headless callers (smoke,
    Phase 2 batch runner) leave it empty — that path skips the modal.
    """
    symbol: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    quantity: float
    side: str
    pnl: float
    pnl_pct: float
    mae: float
    mfe: float
    mae_pct: float
    mfe_pct: float
    ref_pre_trade_id: str | None = None
    user_review: str = ""
