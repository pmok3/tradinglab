"""Chart overlay: horizontal lines for active exit triggers.

Self-contained matplotlib artist family that decorates the primary
price axis with a horizontal line + right-edge label for every priced
trigger of every strategy attached to an open position on the primary
symbol.

The overlay is **not** a Tk widget — it manipulates `matplotlib.Axes`
directly. It also subscribes to evaluator events so external mutations
(attach / detach / fire) trigger a repaint via the caller-supplied
``request_redraw`` callback. Repaint is the caller's responsibility;
the overlay never touches the Tk event loop.

Lifecycle (called from ChartApp at the end of ``_render``):

    overlay.redraw(primary_ax, primary_symbol)

The previous render pass's artists are dropped — ``figure.clear()``
already wiped them, but the python references are released here too
so the overlay's internal map doesn't leak.

Color scheme (themable later; v1 hardcoded):
    LIMIT (target)        : green
    STOP / STOP_LIMIT     : red
    TRAILING_STOP (armed) : orange
    DISARMED              : gray
    FIRED                 : dim gray, dashed
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from matplotlib.text import Text

from tradinglab.exits.evaluator import ExitEvaluator
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    TriggerKind,
)
from tradinglab.exits.spec import resolve_price
from tradinglab.positions.tracker import PositionTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------


_COLOR_LIMIT = "#28a745"     # green
_COLOR_STOP = "#d73a49"      # red
_COLOR_TRAIL = "#fb8c00"     # orange
_COLOR_DISARMED = "#888888"  # gray
_COLOR_FIRED = "#555555"     # dim gray (dashed)


@dataclass(frozen=True)
class OverlayLine:
    """Pure-data description of a single overlay line.

    Computed from the evaluator state — used by the renderer to drive
    matplotlib + by tests to assert what *would* be drawn without
    needing a real figure.
    """

    position_id: str
    leg_id: str
    trigger_id: str
    price: float
    color: str
    linestyle: str
    label: str
    fired: bool


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


def compute_overlay_lines(
    *,
    evaluator: ExitEvaluator,
    tracker: PositionTracker,
    primary_symbol: Optional[str],
) -> List[OverlayLine]:
    """Walk attached strategies on the primary symbol → :class:`OverlayLine`s.

    Skips positions on other symbols, malformed triggers (no resolvable
    price), kinds with no horizontal-line semantics (MARKET, INDICATOR,
    TIME_OF_DAY), and inactive trailing stops (HWM not yet established).
    """
    if not primary_symbol:
        return []
    sym_norm = str(primary_symbol).strip().upper()
    if not sym_norm:
        return []

    out: List[OverlayLine] = []
    open_positions = list(tracker.list_open())
    for pos in open_positions:
        if (pos.symbol or "").strip().upper() != sym_norm:
            continue
        strategy: Optional[ExitStrategy] = evaluator.attached_strategy(pos.id)
        if strategy is None:
            continue
        for leg in strategy.legs:
            for trigger in leg.triggers:
                line = _line_for_trigger(
                    evaluator=evaluator,
                    pos=pos,
                    leg=leg,
                    trigger=trigger,
                )
                if line is not None:
                    out.append(line)
    return out


def _line_for_trigger(
    *,
    evaluator: ExitEvaluator,
    pos,
    leg: ExitLeg,
    trigger: ExitTrigger,
) -> Optional[OverlayLine]:
    """Build a single :class:`OverlayLine` or ``None`` if N/A."""
    kind = trigger.kind
    price: Optional[float] = None
    color = _COLOR_DISARMED
    label_kind: str = ""

    slot = evaluator.trigger_state(pos.id, leg.id, trigger.id)
    armed = bool(slot.armed) if slot is not None else False
    fired = bool(slot.state.fire_count) if slot is not None else False

    if kind is TriggerKind.LIMIT:
        price = resolve_price(trigger, pos)
        color = _COLOR_LIMIT
        label_kind = "LIMIT"
    elif kind is TriggerKind.STOP:
        price = resolve_price(trigger, pos)
        color = _COLOR_STOP
        label_kind = "STOP"
    elif kind is TriggerKind.STOP_LIMIT:
        price = resolve_price(trigger, pos)
        color = _COLOR_STOP
        label_kind = "STOP-LMT"
    elif kind is TriggerKind.TRAILING_STOP:
        if slot is not None and slot.state.trail_price is not None:
            price = float(slot.state.trail_price)
            color = _COLOR_TRAIL
            label_kind = "TRAIL"
        else:
            return None
    else:
        # MARKET / TIME_OF_DAY / INDICATOR — no price line.
        return None

    if price is None:
        return None

    if fired:
        color = _COLOR_FIRED
        linestyle = "--"
    elif not armed:
        color = _COLOR_DISARMED
        linestyle = "-."
    else:
        linestyle = "-"

    leg_label = leg.label or leg.id[:6]
    label = f"{label_kind} {leg_label} @ {price:,.2f}"
    return OverlayLine(
        position_id=pos.id,
        leg_id=leg.id,
        trigger_id=trigger.id,
        price=float(price),
        color=color,
        linestyle=linestyle,
        label=label,
        fired=fired,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class ExitsOverlay:
    """Owns the matplotlib artists for the exit-trigger overlay.

    The lifecycle is "rebuild on every render" — same as the rest of
    the chart family, since ``figure.clear()`` destroys axes between
    renders. This class holds python refs to the artists only so that
    a follow-up ``clear()`` can drop them deterministically (tests,
    teardown).

    External state changes (attach, detach, fire) call
    ``request_redraw()`` — the app installs a debounced
    ``self.after(50, ...)`` callback which calls ``ChartApp._render``,
    which in turn calls ``self.redraw(...)`` again.
    """

    def __init__(
        self,
        *,
        evaluator: ExitEvaluator,
        tracker: PositionTracker,
        request_redraw: Optional[Callable[[], None]] = None,
        enabled: bool = True,
    ) -> None:
        self._evaluator = evaluator
        self._tracker = tracker
        self._request_redraw = request_redraw or (lambda: None)
        self._enabled = bool(enabled)
        # Map position_id -> list[(line, label)] to permit per-position cleanup.
        self._artists: Dict[str, List[Tuple[Line2D, Optional[Text]]]] = {}

        # Subscribe to position events (open / fill / close) so that
        # attaching/detaching strategies and position lifecycle changes
        # trigger a chart repaint. Trail-price ticks land via the
        # normal _render path (driven by bar arrivals) — we don't need
        # a separate per-bar event for them.
        self._unsubscribe = tracker.subscribe(self._on_position_event)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Toggle visibility. Triggers a redraw via the request callback."""
        if bool(enabled) == self._enabled:
            return
        self._enabled = bool(enabled)
        self._request_redraw()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def line_count(self) -> int:
        """Number of currently-rendered overlay lines (testing helper)."""
        return sum(len(v) for v in self._artists.values())

    def clear(self) -> None:
        """Drop every artist reference. Safe to call repeatedly."""
        # We don't call .remove() on the Line2D / Text — figure.clear()
        # is the canonical owner of axes lifetime, and the artists may
        # already be detached from a now-dead axes. Just release refs.
        self._artists.clear()

    def close(self) -> None:
        """Detach from evaluator events. Idempotent."""
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:  # noqa: BLE001
                logger.exception("ExitsOverlay: unsubscribe raised")
            self._unsubscribe = None
        self.clear()

    def redraw(
        self,
        primary_ax: Optional[Axes],
        primary_symbol: Optional[str],
    ) -> List[OverlayLine]:
        """Rebuild artists on ``primary_ax`` for ``primary_symbol``.

        Returns the list of :class:`OverlayLine` descriptors that were
        rendered (testing + diagnostics).
        """
        self.clear()
        if not self._enabled or primary_ax is None or not primary_symbol:
            return []
        try:
            lines = compute_overlay_lines(
                evaluator=self._evaluator,
                tracker=self._tracker,
                primary_symbol=primary_symbol,
            )
        except Exception:  # noqa: BLE001
            logger.exception("ExitsOverlay: compute_overlay_lines raised")
            return []
        for desc in lines:
            try:
                self._draw_one(primary_ax, desc)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "ExitsOverlay: _draw_one raised for %s/%s/%s",
                    desc.position_id, desc.leg_id, desc.trigger_id,
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
            alpha=0.55 if desc.fired else 0.85,
        )
        # Right-edge label. Use blended (axes_x, data_y) coords so the
        # text sticks at the right margin even as xlim shifts.
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
            logger.exception("ExitsOverlay: label render failed")
        bucket = self._artists.setdefault(desc.position_id, [])
        bucket.append((line, label))

    def _on_evaluator_event(self, event: object) -> None:
        """Evaluator subscriber callback. Schedules a redraw."""
        try:
            self._request_redraw()
        except Exception:  # noqa: BLE001
            logger.exception("ExitsOverlay: request_redraw raised")

    def _on_position_event(self, event: object, pos: object = None) -> None:
        """:class:`PositionTracker` subscriber callback. Schedules a redraw.

        Fires for open / fill / close. The overlay reacts by asking
        the host to repaint; the renderer reads fresh state via
        ``compute_overlay_lines`` on its next pass. The tracker's
        subscriber signature is ``(event, position)``; we accept both
        positionally and ignore the payload.
        """
        try:
            self._request_redraw()
        except Exception:  # noqa: BLE001
            logger.exception("ExitsOverlay: request_redraw raised")
