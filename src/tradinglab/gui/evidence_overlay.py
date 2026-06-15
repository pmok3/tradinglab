"""Chart overlay: vertical-line markers at within-last-N-bars evidence bars.

When an entries or exits trigger fires with a ``within_last_bars > 0``
look-back walk that found a match earlier in the window, the engine
attaches a list of :class:`tradinglab.scanner.model.MatchEvidence`
to the audit log record (``meta["evidence"]``). This overlay reads
recent fire records, filters by the primary chart symbol, maps each
evidence ``timestamp`` to a candle index on the primary axis, and draws
a small dashed vertical line + label so the trader sees on the chart
itself which bar a confirmation event resolved on.

Lifecycle: rebuild on every render — same idiom as
:class:`tradinglab.gui.exits_overlay.ExitsOverlay` and
:class:`tradinglab.gui.entries_overlay.EntriesOverlay`. ``figure.clear()``
destroys the axes between renders, so every render rebuilds artists.

Source filtering:

* ``entry_fire`` records carry a top-level ``symbol`` field — filter
  directly on it.
* ``fire`` records (exits) carry only ``position_id``; the overlay
  resolves to a symbol via :meth:`PositionTracker.get`. Closed positions
  are still resolvable (they remain in the tracker's flat dict).

Color scheme:

* ENTRY evidence → green (#1f7a36)
* EXIT  evidence → red   (#a02434)
* Dashed line at the resolved bar's index, alpha 0.55.
* Right-stacked label at the top of the price axis showing
  ``E:{node_id_short}`` or ``X:{node_id_short}`` and the
  short ``"NN bars ago"`` snippet.

Tk-free in pure-logic helpers (``compute_evidence_markers``); the class
itself wraps matplotlib state.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from matplotlib.text import Text

from ..entries.audit import AuditLog as EntriesAuditLog
from ..exits.audit import AuditLog as ExitsAuditLog
from ..positions.tracker import PositionTracker

logger = logging.getLogger(__name__)


_COLOR_ENTRY = "#1f7a36"  # green
_COLOR_EXIT = "#a02434"  # red

_TAIL_LIMIT = 50  # last-N records read from each audit log


@dataclass(frozen=True)
class EvidenceMarker:
    """Pure-data description of one evidence marker.

    Drawn by :meth:`EvidenceOverlay._draw_one`; also returned from
    :func:`compute_evidence_markers` so the layer can be unit-tested
    without matplotlib.

    ``bar_index`` is the candle-index on the primary axis (the same
    ``i`` the chart uses for x-coordinates). When the timestamp can't
    be matched to any candle in the visible window, the marker is
    dropped at compute time.
    """

    source: str  # "entry" or "exit"
    node_id: str
    bar_index: int
    bars_ago: int
    timestamp: str
    color: str
    label: str


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


def _parse_iso_to_utc(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp into a tz-aware UTC datetime.

    Returns ``None`` for empty/invalid input. Naive ISO strings are
    treated as UTC (matches the engine's
    :func:`scanner.engine._bar_timestamp_iso` convention which strips
    the trailing ``Z``).
    """
    if not ts:
        return None
    try:
        # Python 3.11+ accepts trailing "Z" in fromisoformat; for
        # earlier versions we strip it manually.
        s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _candle_timestamp_to_utc(c_ts: Any) -> datetime | None:
    """Return a UTC-aware datetime from a candle's ``date`` attribute.

    Candles store their open time as a ``datetime`` (per
    :class:`tradinglab.models.Candle`). Naive datetimes are treated
    as UTC. Returns ``None`` for an unrecognised value.
    """
    if c_ts is None:
        return None
    if not isinstance(c_ts, datetime):
        return None
    if c_ts.tzinfo is None:
        return c_ts.replace(tzinfo=timezone.utc)
    return c_ts.astimezone(timezone.utc)


def _find_bar_index_by_timestamp(
    candles: list[Any], target_ts: datetime
) -> int | None:
    """Return the candle index whose timestamp matches ``target_ts``.

    Match is up-to-the-second equality after both sides are converted
    to UTC. The lookup is linear; with ~1000 candles per chart and a
    handful of evidence entries per render, this is well under a
    millisecond.
    """
    if not candles:
        return None
    target_sec = int(target_ts.timestamp())
    for i, c in enumerate(candles):
        cdt = _candle_timestamp_to_utc(getattr(c, "date", None))
        if cdt is None:
            continue
        if int(cdt.timestamp()) == target_sec:
            return i
    return None


def _short(node_id: str, n: int = 6) -> str:
    return (node_id[:n]) if node_id else "?"


def _format_bars_ago(bars_ago: int) -> str:
    if bars_ago <= 0:
        return "now"
    if bars_ago == 1:
        return "1 bar"
    return f"{int(bars_ago)} bars"


def _evidence_records_for_symbol(
    records: Iterable[dict[str, Any]],
    *,
    primary_symbol: str,
    tracker: PositionTracker | None,
    source: str,
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Filter audit records to those for ``primary_symbol`` carrying evidence.

    Returns ``[(record, evidence_list), ...]`` for matching records.
    For ``source="entry"`` the symbol filter uses the record's
    ``symbol`` field directly. For ``source="exit"`` the filter
    resolves ``position_id → tracker.get(...).symbol`` (dropped if the
    position is unknown — defensive).
    """
    out: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    target = primary_symbol.strip().upper()
    if not target:
        return out
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if source == "entry" and rec.get("kind") != "entry_fire":
            continue
        if source == "exit" and rec.get("kind") != "fire":
            continue
        meta = rec.get("meta")
        if not isinstance(meta, dict):
            continue
        evidence = meta.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            continue

        if source == "entry":
            sym = (rec.get("symbol") or "").strip().upper()
        else:
            pid = rec.get("position_id")
            if not pid or tracker is None:
                continue
            try:
                pos = tracker.get(pid)
            except Exception:  # noqa: BLE001 - defensive
                pos = None
            if pos is None:
                continue
            sym = (getattr(pos, "symbol", "") or "").strip().upper()
        if sym != target:
            continue
        out.append((rec, evidence))
    return out


def compute_evidence_markers(
    *,
    primary_symbol: str | None,
    primary_candles: list[Any] | None,
    entries_audit: EntriesAuditLog | None,
    exits_audit: ExitsAuditLog | None,
    tracker: PositionTracker | None,
    tail: int = _TAIL_LIMIT,
) -> list[EvidenceMarker]:
    """Pure-logic helper: derive evidence markers for the primary chart.

    Reads up to ``tail`` recent records from each provided audit log,
    filters to the primary symbol, parses evidence timestamps, and
    matches each to a candle index. Markers without a candle match
    (timestamp falls outside the visible candle list) are dropped.

    Returns markers sorted by ``bar_index`` ascending so duplicate
    indices stack deterministically when the renderer collides them.
    """
    if not primary_symbol or not primary_candles:
        return []

    out: list[EvidenceMarker] = []

    sources: list[tuple[str, Any | None, str]] = [
        ("entry", entries_audit, _COLOR_ENTRY),
        ("exit", exits_audit, _COLOR_EXIT),
    ]
    for source, audit, color in sources:
        if audit is None:
            continue
        try:
            recs = audit.tail(tail)
        except Exception:  # noqa: BLE001
            logger.exception(
                "EvidenceOverlay: %s audit.tail raised", source
            )
            continue
        matches = _evidence_records_for_symbol(
            recs,
            primary_symbol=primary_symbol,
            tracker=tracker,
            source=source,
        )
        for _rec, evidence in matches:
            for ev in evidence:
                if not isinstance(ev, dict):
                    continue
                ts_iso = ev.get("timestamp") or ""
                target_dt = _parse_iso_to_utc(ts_iso)
                if target_dt is None:
                    continue
                idx = _find_bar_index_by_timestamp(primary_candles, target_dt)
                if idx is None:
                    continue
                bars_ago = int(ev.get("bars_ago") or 0)
                node_id = str(ev.get("node_id") or "")
                tag = "E" if source == "entry" else "X"
                label = (
                    f"{tag}:{_short(node_id)} {_format_bars_ago(bars_ago)}"
                )
                out.append(
                    EvidenceMarker(
                        source=source,
                        node_id=node_id,
                        bar_index=idx,
                        bars_ago=bars_ago,
                        timestamp=ts_iso,
                        color=color,
                        label=label,
                    )
                )
    out.sort(key=lambda m: (m.bar_index, m.source))
    return out


# ---------------------------------------------------------------------------
# Matplotlib wrapper
# ---------------------------------------------------------------------------


class EvidenceOverlay:
    """Owns the matplotlib artists for the within-last-N-bars markers.

    Lifecycle mirrors the existing entries/exits overlays — every
    render rebuilds the artists, ``clear()`` releases python refs
    (the figure clear destroys the axes themselves).

    The class is purposely thin: most logic lives in
    :func:`compute_evidence_markers` which is pure (no Tk / matplotlib
    state) and exhaustively unit-tested. The class only owns the
    artist refs and the enable flag.
    """

    def __init__(
        self,
        *,
        entries_audit: EntriesAuditLog | None = None,
        exits_audit: ExitsAuditLog | None = None,
        tracker: PositionTracker | None = None,
        request_redraw: Callable[[], None] | None = None,
        enabled: bool = True,
    ) -> None:
        self._entries_audit = entries_audit
        self._exits_audit = exits_audit
        self._tracker = tracker
        self._request_redraw = request_redraw or (lambda: None)
        self._enabled = bool(enabled)
        self._artists: list[tuple[Line2D, Text | None]] = []

    # ---- public API ----

    def set_enabled(self, enabled: bool) -> None:
        if bool(enabled) == self._enabled:
            return
        self._enabled = bool(enabled)
        try:
            self._request_redraw()
        except Exception:  # noqa: BLE001
            logger.exception("EvidenceOverlay: request_redraw raised")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def marker_count(self) -> int:
        return len(self._artists)

    def clear(self) -> None:
        """Detach every marker artist from its axes, then drop refs.

        Detaching (not merely dropping refs) makes the overlay safe to clear
        WITHOUT a surrounding ``figure.clear()`` — required by the
        topology-preserving paint pipeline fast path
        (``docs/PAINT_PIPELINE_REFACTOR.md``). Idempotent + defensive: an
        artist already detached raises on ``.remove()``, which is swallowed.
        End state is identical to the old ref-drop in the current
        ``figure.clear()`` flow.
        """
        for line, label in self._artists:
            for art in (line, label):
                if art is not None:
                    try:
                        art.remove()
                    except Exception:  # noqa: BLE001
                        pass
        self._artists.clear()

    def close(self) -> None:
        self.clear()

    def redraw(
        self,
        primary_ax: Axes | None,
        primary_symbol: str | None,
        primary_candles: list[Any] | None,
    ) -> list[EvidenceMarker]:
        """Rebuild markers on ``primary_ax`` for ``primary_symbol``.

        Returns the list of :class:`EvidenceMarker` descriptors that
        were rendered (testing + diagnostics).
        """
        self.clear()
        if not self._enabled or primary_ax is None:
            return []
        markers = compute_evidence_markers(
            primary_symbol=primary_symbol,
            primary_candles=primary_candles,
            entries_audit=self._entries_audit,
            exits_audit=self._exits_audit,
            tracker=self._tracker,
        )
        for m in markers:
            try:
                self._draw_one(primary_ax, m)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "EvidenceOverlay: _draw_one raised for %s @ idx=%d",
                    m.source, m.bar_index,
                )
        return markers

    # ---- internals ----

    def _draw_one(self, ax: Axes, marker: EvidenceMarker) -> None:
        line = ax.axvline(
            x=marker.bar_index,
            color=marker.color,
            linestyle="--",
            linewidth=1.0,
            alpha=0.55,
            zorder=3,
        )
        label: Text | None = None
        try:
            from matplotlib.transforms import blended_transform_factory
            tr = blended_transform_factory(ax.transData, ax.transAxes)
            label = ax.text(
                marker.bar_index,
                0.99,
                marker.label,
                transform=tr,
                ha="left", va="top",
                fontsize=7, color=marker.color,
                rotation=90,
                clip_on=True,
                zorder=4,
            )
        except Exception:  # noqa: BLE001
            logger.exception("EvidenceOverlay: label render failed")
        self._artists.append((line, label))


__all__ = [
    "EvidenceMarker",
    "EvidenceOverlay",
    "compute_evidence_markers",
]
