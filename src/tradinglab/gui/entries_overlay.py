"""Chart overlay: horizontal lines for armed entries + pending entry orders.

Mirrors :mod:`tradinglab.gui.exits_overlay` for the entries domain.
The overlay decorates the primary price axis with one horizontal line +
right-edge label per:

* **Armed strategy** with a price-bearing trigger (LIMIT, STOP,
  STOP_LIMIT) whose universe matches the primary symbol — drawn
  dashed/dotted to communicate "watching" semantics.
* **Pending entry order** sitting in the paper-broker engine
  (``target_kind=PENDING_ENTRY``) for the primary symbol — drawn solid
  with a ``PENDING <KIND>`` prefix to communicate "live working order".

Color scheme:
    LONG  trigger price → green (#28a745)
    SHORT trigger price → red   (#d73a49)
    Pending → solid line; Armed (no order yet) → dashed/dotted.

INDICATOR / SCANNER_ALERT / MARKET triggers have no price coordinate
and are therefore not rendered.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from matplotlib.text import Text

from ..entries.evaluator import EntryEvaluator
from ..entries.model import Direction, EntryStrategy, TriggerKind
from ..exits.paper_engine import OrderTargetKind, PaperBrokerEngine, PaperOrderKind

logger = logging.getLogger(__name__)


_COLOR_LONG = "#28a745"
_COLOR_SHORT = "#d73a49"
_COLOR_DISARMED = "#888888"


@dataclass(frozen=True)
class OverlayLine:
    """Pure-data description of one entries overlay line.

    Drawn by :class:`EntriesOverlay._draw_one`; also returned from
    :func:`compute_overlay_lines` for testing without matplotlib.
    """

    kind: str  # "armed_limit" / "armed_stop" / "pending_limit" / "pending_stop" / "pending_stop_limit"
    strategy_id: Optional[str]
    pending_order_id: Optional[str]
    symbol: str
    direction: Direction
    price: float
    color: str
    linestyle: str
    label: str
    pending: bool


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


def _strategy_targets_symbol(s: EntryStrategy, symbol: str) -> bool:
    sym = symbol.strip().upper()
    if not sym:
        return False
    if s.universe.from_attached_chart:
        return True
    if s.universe.symbols:
        return sym in {x.upper() for x in s.universe.symbols}
    if s.universe.scanner_id:
        # We don't know the scanner's symbol set without a join; the
        # safer behavior is to NOT decorate the chart for scanner-fed
        # strategies. Pending orders that result from such alerts are
        # still drawn (they have an explicit symbol).
        return False
    return False


def compute_overlay_lines(
    *,
    evaluator: EntryEvaluator,
    paper_engine: Optional[PaperBrokerEngine],
    primary_symbol: Optional[str],
) -> List[OverlayLine]:
    """Build all overlay-line descriptors for the primary chart.

    Returns the complete list (armed + pending), in deterministic
    insertion order (armed first, then pending). Skips:

    * empty / falsy ``primary_symbol``,
    * strategies with no matching universe,
    * MARKET / INDICATOR / SCANNER_ALERT triggers (no price),
    * strategies whose chosen trigger field is ``None``.
    """
    if not primary_symbol:
        return []
    sym = primary_symbol.strip().upper()
    if not sym:
        return []

    out: List[OverlayLine] = []

    # 1) Armed strategies (snapshot via evaluator).
    armed_ids = set()
    try:
        armed_ids = set(evaluator.armed_strategies())
    except Exception:  # noqa: BLE001
        logger.exception("EntriesOverlay: armed_strategies raised")
    for sid in armed_ids:
        try:
            s = evaluator.get_strategy(sid)
        except Exception:  # noqa: BLE001
            continue
        if s is None or not s.enabled:
            continue
        if not _strategy_targets_symbol(s, sym):
            continue
        line = _line_for_armed_strategy(s, sym)
        if line is not None:
            out.append(line)

    # 2) Pending entry orders for this symbol (broker working orders).
    if paper_engine is not None:
        try:
            pending = paper_engine.pending_orders_for_symbol(sym)
        except Exception:  # noqa: BLE001
            logger.exception("EntriesOverlay: pending_orders_for_symbol raised")
            pending = []
        for po in pending:
            if po.target_kind != OrderTargetKind.PENDING_ENTRY:
                continue
            for line in _lines_for_pending_order(po, sym):
                out.append(line)

    return out


def _line_for_armed_strategy(
    s: EntryStrategy, symbol: str,
) -> Optional[OverlayLine]:
    """Return one armed-line descriptor or ``None`` if N/A."""
    kind = s.trigger.kind
    direction = s.direction
    color = _COLOR_LONG if direction is Direction.LONG else _COLOR_SHORT
    if kind is TriggerKind.LIMIT:
        if s.trigger.price is None:
            return None
        return OverlayLine(
            kind="armed_limit",
            strategy_id=s.id,
            pending_order_id=None,
            symbol=symbol,
            direction=direction,
            price=float(s.trigger.price),
            color=color,
            linestyle="--",
            label=f"ARMED LIMIT {s.name} @ {s.trigger.price:,.2f}",
            pending=False,
        )
    if kind is TriggerKind.STOP:
        if s.trigger.stop_price is None:
            return None
        return OverlayLine(
            kind="armed_stop",
            strategy_id=s.id,
            pending_order_id=None,
            symbol=symbol,
            direction=direction,
            price=float(s.trigger.stop_price),
            color=color,
            linestyle=":",
            label=f"ARMED STOP {s.name} @ {s.trigger.stop_price:,.2f}",
            pending=False,
        )
    if kind is TriggerKind.STOP_LIMIT:
        if s.trigger.stop_price is None:
            return None
        return OverlayLine(
            kind="armed_stop_limit",
            strategy_id=s.id,
            pending_order_id=None,
            symbol=symbol,
            direction=direction,
            price=float(s.trigger.stop_price),
            color=color,
            linestyle=":",
            label=f"ARMED STOP-LMT {s.name} @ {s.trigger.stop_price:,.2f}",
            pending=False,
        )
    return None


def _lines_for_pending_order(po: Any, symbol: str) -> List[OverlayLine]:
    """Build one or two lines for a pending PaperOrder.

    A STOP_LIMIT pending order has both a stop price and a limit price;
    we draw both (limit dashed, stop dotted, both solid-color since the
    order is live).
    """
    side = po.position_side
    direction = (
        Direction.LONG if (side is not None and str(side) == "long")
        else Direction.SHORT
    )
    color = _COLOR_LONG if direction is Direction.LONG else _COLOR_SHORT
    out: List[OverlayLine] = []
    if po.kind is PaperOrderKind.LIMIT and po.price is not None:
        out.append(OverlayLine(
            kind="pending_limit",
            strategy_id=po.strategy_id,
            pending_order_id=po.id,
            symbol=symbol,
            direction=direction,
            price=float(po.price),
            color=color,
            linestyle="-",
            label=f"PENDING LIMIT {po.qty:g} @ {po.price:,.2f}",
            pending=True,
        ))
    elif po.kind is PaperOrderKind.STOP and po.price is not None:
        out.append(OverlayLine(
            kind="pending_stop",
            strategy_id=po.strategy_id,
            pending_order_id=po.id,
            symbol=symbol,
            direction=direction,
            price=float(po.price),
            color=color,
            linestyle="-",
            label=f"PENDING STOP {po.qty:g} @ {po.price:,.2f}",
            pending=True,
        ))
    elif po.kind is PaperOrderKind.STOP_LIMIT:
        if po.price is not None:
            out.append(OverlayLine(
                kind="pending_stop_limit_stop",
                strategy_id=po.strategy_id,
                pending_order_id=po.id,
                symbol=symbol,
                direction=direction,
                price=float(po.price),
                color=color,
                linestyle="-",
                label=f"PENDING STOP-LMT {po.qty:g} stop@{po.price:,.2f}",
                pending=True,
            ))
        if po.limit_price is not None:
            out.append(OverlayLine(
                kind="pending_stop_limit_limit",
                strategy_id=po.strategy_id,
                pending_order_id=po.id,
                symbol=symbol,
                direction=direction,
                price=float(po.limit_price),
                color=color,
                linestyle="--",
                label=f"PENDING STOP-LMT lmt@{po.limit_price:,.2f}",
                pending=True,
            ))
    return out


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class EntriesOverlay:
    """Owns the matplotlib artists for the entries-overlay layer.

    Lifecycle is "rebuild on every render". The class only keeps weak
    Python refs to artists so a follow-up :meth:`clear` can drop them
    deterministically.
    """

    def __init__(
        self,
        *,
        evaluator: EntryEvaluator,
        paper_engine: Optional[PaperBrokerEngine] = None,
        request_redraw: Optional[Callable[[], None]] = None,
        enabled: bool = True,
    ) -> None:
        self._evaluator = evaluator
        self._paper_engine = paper_engine
        self._request_redraw = request_redraw or (lambda: None)
        self._enabled = bool(enabled)
        # Map (kind, key) → list[(line, label)] purely for cleanup.
        self._artists: Dict[str, List[Tuple[Line2D, Optional[Text]]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        if bool(enabled) == self._enabled:
            return
        self._enabled = bool(enabled)
        try:
            self._request_redraw()
        except Exception:  # noqa: BLE001
            logger.exception("EntriesOverlay: request_redraw raised")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def line_count(self) -> int:
        """Currently-rendered line count (testing helper)."""
        return sum(len(v) for v in self._artists.values())

    def clear(self) -> None:
        self._artists.clear()

    def close(self) -> None:
        self.clear()

    def redraw(
        self,
        primary_ax: Optional[Axes],
        primary_symbol: Optional[str],
    ) -> List[OverlayLine]:
        self.clear()
        if not self._enabled or primary_ax is None or not primary_symbol:
            return []
        try:
            lines = compute_overlay_lines(
                evaluator=self._evaluator,
                paper_engine=self._paper_engine,
                primary_symbol=primary_symbol,
            )
        except Exception:  # noqa: BLE001
            logger.exception("EntriesOverlay: compute_overlay_lines raised")
            return []
        for desc in lines:
            try:
                self._draw_one(primary_ax, desc)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "EntriesOverlay: _draw_one raised for %s/%s",
                    desc.kind, desc.strategy_id or desc.pending_order_id,
                )
        return lines

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _draw_one(self, ax: Axes, desc: OverlayLine) -> None:
        line = ax.axhline(
            y=desc.price,
            color=desc.color,
            linestyle=desc.linestyle,
            linewidth=1.0,
            zorder=4,
            alpha=0.85 if desc.pending else 0.7,
        )
        label: Optional[Text] = None
        try:
            from matplotlib.transforms import blended_transform_factory
            tr = blended_transform_factory(ax.transAxes, ax.transData)
            label = ax.text(
                1.0, desc.price, " " + desc.label,
                transform=tr,
                ha="left", va="center",
                fontsize=8, color=desc.color,
                clip_on=False,
                zorder=5,
            )
        except Exception:  # noqa: BLE001
            logger.exception("EntriesOverlay: label render failed")
        bucket_key = desc.pending_order_id or desc.strategy_id or desc.kind
        bucket = self._artists.setdefault(bucket_key, [])
        bucket.append((line, label))
