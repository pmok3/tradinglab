"""Canonical side-of-trade value type.

The codebase historically uses three different vocabularies for the
same concept "is this a long-side or short-side position":

- ``"long"`` / ``"short"`` (``exits/spec.py`` — 30+ sites).
- ``"buy"`` / ``"sell"`` (``strategy_tester/evaluator.py`` — entry/exit
  fills; matches :class:`backtest.orders.Side` string values).
- :class:`backtest.orders.Side` enum (``Side.BUY`` / ``Side.SELL``,
  surfaced via :class:`Order.side` / :class:`Fill.side`).

This module supplies one normalised type so the next sign-flip /
favorable-price branch you write doesn't drift the way the existing
30+ sites have. Existing call sites stay on their old vocabulary;
new code adopts :class:`Side`; old sites migrate opportunistically.

Conversion contract:

- ``Side.from_str("long" | "buy" | "LONG" | "BUY" | …)``  → ``Side.LONG``
- ``Side.from_str("short" | "sell" | …)``                 → ``Side.SHORT``
- ``Side.from_order_side(OrderSide.BUY)``                 → ``Side.LONG``
  (assumes opening fill — a BUY order opens a LONG position)
- ``Side.LONG.as_long_short()``                           → ``"long"``
- ``Side.LONG.as_buy_sell()``                             → ``"buy"``
- ``Side.LONG.as_order_side()``                           → ``OrderSide.BUY``

Numeric helpers (eliminate inline ``1 if long else -1`` branches):

- ``side.sign``                       → ``+1`` / ``-1``
- ``side.is_long`` / ``side.is_short`` → bool
- ``side.favorable_price(bar)``        → ``bar.high`` (long) / ``bar.low`` (short)
- ``side.unfavorable_price(bar)``      → ``bar.low``  (long) / ``bar.high`` (short)
- ``side.adverse_excursion_price(bar)``   → alias for ``unfavorable_price`` (MAE)
- ``side.favorable_excursion_price(bar)`` → alias for ``favorable_price``   (MFE)

The MAE/MFE aliases are intentional — at MAE/MFE call sites the
self-documenting alias name is more important than the marginal
overhead of one extra attribute access.

See ``files/generalization-audit.md`` item #10 for the motivating
audit and ``core/side.spec.md`` for the migration policy.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..backtest.orders import Side as OrderSide


_LONG_ALIASES = frozenset({"long", "buy", "l", "+1", "1"})
_SHORT_ALIASES = frozenset({"short", "sell", "s", "-1"})


class Side(Enum):
    """Direction a position is held in. ``+1`` long, ``-1`` short."""

    LONG = 1
    SHORT = -1

    # -- factories -------------------------------------------------------

    @classmethod
    def from_str(cls, value: str) -> Side:
        """Parse any of the historic vocabularies into a :class:`Side`.

        Accepts ``"long"`` / ``"short"`` (exits/spec vocabulary),
        ``"buy"`` / ``"sell"`` (strategy_tester evaluator + Order/Fill
        string values), single-letter shorthands ``"l"`` / ``"s"``, and
        signed numeric strings ``"+1"`` / ``"-1"``. Case-insensitive;
        whitespace tolerated.

        Raises :class:`ValueError` with a helpful message on anything
        else — silent coercion would defeat the whole point of the
        value type.
        """
        if not isinstance(value, str):  # type: ignore[unreachable]
            raise ValueError(
                f"Side.from_str expected a string, got {type(value).__name__!r}"
            )
        token = value.strip().lower()
        if token in _LONG_ALIASES:
            return cls.LONG
        if token in _SHORT_ALIASES:
            return cls.SHORT
        raise ValueError(
            f"Side.from_str: cannot parse {value!r}. "
            f"Accepted: long/short, buy/sell, l/s, +1/-1 (case-insensitive)."
        )

    @classmethod
    def from_order_side(cls, order_side: OrderSide) -> Side:
        """Map :class:`backtest.orders.Side` to :class:`Side`.

        Assumes the order is the **opening** fill — a BUY opens a LONG
        position; a SELL opens a SHORT. (Closing-fill semantics flip
        the mapping; convert at the boundary using whatever caller
        context disambiguates open vs close.)
        """
        # Delegate to from_str so the order_side's string value
        # (``"buy"`` / ``"sell"``) goes through the same parser.
        return cls.from_str(order_side.value)

    @classmethod
    def from_sign(cls, sign: int | float) -> Side:
        """Map a signed scalar to :class:`Side`. ``sign > 0`` → LONG,
        ``sign < 0`` → SHORT. ``sign == 0`` raises :class:`ValueError`."""
        if sign > 0:
            return cls.LONG
        if sign < 0:
            return cls.SHORT
        raise ValueError("Side.from_sign: zero is not a valid side")

    # -- adapters --------------------------------------------------------

    def as_long_short(self) -> str:
        """Return the ``"long"`` / ``"short"`` form (exits/spec.py vocab)."""
        return "long" if self is Side.LONG else "short"

    def as_buy_sell(self) -> str:
        """Return the ``"buy"`` / ``"sell"`` form (Order.side string vocab)."""
        return "buy" if self is Side.LONG else "sell"

    def as_order_side(self) -> OrderSide:
        """Return the :class:`backtest.orders.Side` enum value."""
        # Local import — keep core/ free of backtest/ at import-time
        # (core layer rule: no app-wide-graph imports at module load).
        from ..backtest.orders import Side as OrderSide

        return OrderSide.BUY if self is Side.LONG else OrderSide.SELL

    # -- numeric helpers -------------------------------------------------

    @property
    def sign(self) -> int:
        """``+1`` for LONG, ``-1`` for SHORT. Replaces the
        ``1 if side == "buy" else -1`` branch."""
        return self.value

    @property
    def is_long(self) -> bool:
        return self is Side.LONG

    @property
    def is_short(self) -> bool:
        return self is Side.SHORT

    def opposite(self) -> Side:
        """The flipped side. Handy for ``exit_side = side.opposite()``."""
        return Side.SHORT if self is Side.LONG else Side.LONG

    # -- bar-price helpers ----------------------------------------------

    def favorable_price(self, bar: Any) -> float:
        """``bar.high`` for LONG, ``bar.low`` for SHORT.

        ``bar`` is any object with ``.high`` / ``.low`` attributes
        (e.g. :class:`models.Candle`, :class:`exits.spec.Bar`, a
        numpy-array-row wrapper).
        """
        return float(bar.high if self is Side.LONG else bar.low)

    def unfavorable_price(self, bar: Any) -> float:
        """``bar.low`` for LONG, ``bar.high`` for SHORT.

        The "adverse-excursion" extreme — opposite of
        :meth:`favorable_price`.
        """
        return float(bar.low if self is Side.LONG else bar.high)

    def adverse_excursion_price(self, bar: Any) -> float:
        """Alias for :meth:`unfavorable_price`. Self-documents MAE math."""
        return self.unfavorable_price(bar)

    def favorable_excursion_price(self, bar: Any) -> float:
        """Alias for :meth:`favorable_price`. Self-documents MFE math."""
        return self.favorable_price(bar)


__all__ = ["Side"]
