"""ChartStack alert engine — four-tier visual + audio attention surface.

See `alerts.spec.md` and §2.4 of the synthesis for the full
tier table. This module owns the per-card alert evaluation, the
audio rate-limiter (2 chimes / 10s with Tier-3 bypass), and the
time-of-day gating (09:30–09:35 ET off; 09:35–10:00 tightened;
10:00+ defaults).

Design constraints honored here:

* **Pure-function evaluators.** Each tier evaluator takes a
  fixed shape (bars, position, scanner row, now) and returns an
  :class:`AlertResult` or ``None``. No I/O, no Tk vars; the
  panel composes these by threading the right inputs.
* **Engine is owner-agnostic.** :class:`AlertEngine` is created
  with no constructor args and reads thresholds via the
  settings adapter on each call. Unit tests construct it with
  ``monkeypatch.setattr(_adapter, "get", ...)`` for fast iteration.
* **Audio is best-effort.** ``winsound`` is Windows-only; the
  engine guards against ``ImportError`` and noop's on other
  platforms so unit tests stay portable.
* **Tier-3 bypasses the rate limiter, not the mute switch.** The
  trader can mute everything (e.g. running a long simulation); a
  bypassed rate-limit on a muted Tier-3 still produces no sound.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..colors import CAUTION_YELLOW, ERROR_RED, INFO_BLUE, WARN_AMBER
from . import settings_adapter as _adapter

_LOG = logging.getLogger(__name__)

# Eastern-time offset is captured at module load via ``zoneinfo``;
# when zoneinfo isn't available (paranoid CI), fall back to a fixed
# UTC-4 offset since we only need a rough ToD gate at 09:30 ET.
try:  # pragma: no cover - import guard
    from zoneinfo import ZoneInfo  # type: ignore
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback
    _ET = _dt.timezone(_dt.timedelta(hours=-4))


class AlertTier(Enum):
    """Severity tiers for ChartStack alerts.

    Higher integer = higher priority. The engine surfaces only
    the highest-tier alert per card at any moment (lower tiers
    are still recorded in :attr:`AlertResult.subordinate` for
    introspection but the visible tint is the max).
    """

    NONE = 0
    TIER_4_YELLOW = 1   # Earnings T-1, ex-div today (badge only).
    TIER_1_AMBER = 2    # RVOL spike, ATR expansion, HA-flat flip.
    TIER_2_BLUE = 3     # PMH/PML break, new scanner edge (1 chime).
    TIER_3_RED = 4      # Stop proximity, P&L zero-cross, MAE ≥ 1R.


_TIER_COLOR = {
    AlertTier.TIER_4_YELLOW: CAUTION_YELLOW,
    AlertTier.TIER_1_AMBER: WARN_AMBER,
    AlertTier.TIER_2_BLUE: INFO_BLUE,
    AlertTier.TIER_3_RED: ERROR_RED,
}


@dataclass(frozen=True)
class AlertResult:
    """Outcome of one evaluation cycle for one card."""

    tier: AlertTier
    """Highest-severity tier fired this cycle."""

    rule_ids: tuple[str, ...] = ()
    """Stable identifiers of every rule that fired (for logging /
    tests / a future audit panel). Order is firing order."""

    badge: str | None = None
    """Optional short-text badge for the header row (used by
    Tier-4 for `T-1` / `EX-DIV`)."""

    @property
    def color(self) -> str | None:
        return _TIER_COLOR.get(self.tier)

    @property
    def is_active(self) -> bool:
        return self.tier is not AlertTier.NONE


# Single instance reused for "no alert" — saves an allocation
# per card per tick across a 5-card / 10 fps steady state.
_NO_ALERT = AlertResult(tier=AlertTier.NONE)


# ---------------------------------------------------------------------------
# Per-tier pure evaluators
# ---------------------------------------------------------------------------


def _close(bar: Any) -> float | None:
    """Best-effort close getter (handles attr-bars + dict-bars + None)."""
    if bar is None:
        return None
    c = getattr(bar, "close", None)
    if c is None and isinstance(bar, dict):
        c = bar.get("close")
    try:
        return float(c) if c is not None else None
    except (TypeError, ValueError):
        return None


def _high(bar: Any) -> float | None:
    h = getattr(bar, "high", None)
    if h is None and isinstance(bar, dict):
        h = bar.get("high")
    try:
        return float(h) if h is not None else None
    except (TypeError, ValueError):
        return None


def _low(bar: Any) -> float | None:
    lo = getattr(bar, "low", None)
    if lo is None and isinstance(bar, dict):
        lo = bar.get("low")
    try:
        return float(lo) if lo is not None else None
    except (TypeError, ValueError):
        return None


def _volume(bar: Any) -> float | None:
    v = getattr(bar, "volume", None)
    if v is None and isinstance(bar, dict):
        v = bar.get("volume")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _session(bar: Any) -> str | None:
    s = getattr(bar, "session", None)
    if s is None and isinstance(bar, dict):
        s = bar.get("session")
    if isinstance(s, str):
        return s.lower()
    return None


def _regular_bars(bars: Sequence[Any]) -> list[Any]:
    """Return only the regular-session subset (filters out pre/post)."""
    return [b for b in bars if _session(b) in (None, "regular", "rth")]


def evaluate_tier1_rvol_spike(
    bars: Sequence[Any],
    *,
    interval_minutes: int,
    rvol_1m_threshold: float,
    rvol_5m_threshold: float,
) -> str | None:
    """Tier-1: relative-volume spike on the most recent regular bar.

    Compares the last bar's volume to a rolling 20-bar mean.
    The threshold differs by interval (1m = 2.5×, 5m = 1.8×).
    Returns the rule id when fired, else ``None``.
    """
    regs = _regular_bars(bars)
    if len(regs) < 21:
        return None
    last_v = _volume(regs[-1])
    if last_v is None or last_v <= 0:
        return None
    window = regs[-21:-1]
    vols = [v for v in (_volume(b) for b in window) if v is not None and v > 0]
    if len(vols) < 10:
        return None
    mean_v = sum(vols) / len(vols)
    if mean_v <= 0:
        return None
    rvol = last_v / mean_v
    threshold = rvol_1m_threshold if interval_minutes <= 1 else rvol_5m_threshold
    if rvol >= threshold:
        return "tier1_rvol_spike"
    return None


def evaluate_tier1_atr_expansion(
    bars: Sequence[Any],
    *,
    atr_threshold: float,
) -> str | None:
    """Tier-1: last bar's true range ≥ ``atr_threshold`` × ATR(14).

    Uses simple Wilder-style averaging on the trailing 14 regular bars.
    Requires at least 15 regular bars to evaluate.
    """
    regs = _regular_bars(bars)
    if len(regs) < 15:
        return None
    trs: list[float] = []
    prev_close = _close(regs[-16]) if len(regs) >= 16 else _close(regs[-15])
    for b in regs[-15:]:
        h = _high(b)
        lo = _low(b)
        c = _close(b)
        if h is None or lo is None or c is None:
            return None
        if prev_close is None:
            tr = h - lo
        else:
            tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        trs.append(tr)
        prev_close = c
    if len(trs) < 15:
        return None
    last_tr = trs[-1]
    atr = sum(trs[:-1]) / 14.0
    if atr <= 0:
        return None
    if last_tr / atr >= atr_threshold:
        return "tier1_atr_expansion"
    return None


def evaluate_tier2_pmh_pml_break(
    bars: Sequence[Any],
) -> str | None:
    """Tier-2: first regular-session close above pre-market high
    (or below pre-market low).

    Edge-triggered semantics live in the engine — this evaluator
    only checks the *current* state; the engine compares to the
    previous tick to fire once.
    """
    if not bars:
        return None
    pmh = None
    pml = None
    for b in bars:
        sess = _session(b)
        if sess in ("pre", "premarket", "pre_market"):
            h = _high(b)
            lo = _low(b)
            if h is not None and (pmh is None or h > pmh):
                pmh = h
            if lo is not None and (pml is None or lo < pml):
                pml = lo
    if pmh is None and pml is None:
        return None
    last_reg = None
    for b in reversed(bars):
        if _session(b) in (None, "regular", "rth"):
            last_reg = b
            break
    if last_reg is None:
        return None
    c = _close(last_reg)
    if c is None:
        return None
    if pmh is not None and c > pmh:
        return "tier2_pmh_break"
    if pml is not None and c < pml:
        return "tier2_pml_break"
    return None


def evaluate_tier2_new_scanner_edge(
    scanner_row: Any,
) -> str | None:
    """Tier-2: scanner edge first detected this tick.

    ``scanner_row`` is a :class:`MatchRow` (or any object with
    ``is_new``). Returns the rule id when ``is_new`` is True.
    """
    if scanner_row is None:
        return None
    is_new = getattr(scanner_row, "is_new", None)
    if is_new is None and isinstance(scanner_row, dict):
        is_new = scanner_row.get("is_new")
    if bool(is_new):
        return "tier2_new_scanner_edge"
    return None


def evaluate_tier3_stop_proximity(
    bars: Sequence[Any],
    *,
    position: Any,
    atr_window: float,
) -> str | None:
    """Tier-3: last price within ``atr_window`` × ATR(14) of the
    position's protective stop. Long stop is below entry; short
    stop is above. Requires both ``stop_price`` on the position
    and a non-trivial ATR.
    """
    if position is None:
        return None
    stop = getattr(position, "stop_price", None)
    if stop is None or stop <= 0:
        return None
    regs = _regular_bars(bars)
    if not regs:
        return None
    last_c = _close(regs[-1])
    if last_c is None:
        return None
    # Reuse the simple ATR calculation from the tier-1 path.
    trs: list[float] = []
    prev_close = _close(regs[-16]) if len(regs) >= 16 else _close(regs[0])
    for b in regs[-14:]:
        h = _high(b)
        lo = _low(b)
        c = _close(b)
        if h is None or lo is None or c is None:
            continue
        if prev_close is None:
            tr = h - lo
        else:
            tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        trs.append(tr)
        prev_close = c
    if not trs:
        return None
    atr = sum(trs) / len(trs)
    if atr <= 0:
        return None
    if abs(last_c - float(stop)) <= atr_window * atr:
        return "tier3_stop_proximity"
    return None


def evaluate_tier3_pnl_zero_cross(
    *,
    position: Any,
    prev_unrealized: float | None,
) -> str | None:
    """Tier-3: unrealized P&L crossed zero this tick.

    ``prev_unrealized`` is the previous-tick reading kept by the
    engine. Sign-change → fire. Sign-stable or NaN → quiet.
    """
    if position is None:
        return None
    cur = getattr(position, "unrealized_pnl", None)
    if cur is None:
        return None
    try:
        cur_f = float(cur)
    except (TypeError, ValueError):
        return None
    if prev_unrealized is None:
        return None
    if (cur_f >= 0 and prev_unrealized < 0) or (cur_f < 0 and prev_unrealized >= 0):
        return "tier3_pnl_zero_cross"
    return None


def evaluate_tier3_mae_one_r(
    *,
    position: Any,
) -> str | None:
    """Tier-3: max adverse excursion meets or exceeds 1R.

    Reads ``position.mae`` (already 1R-normalized by the position
    tracker; if not available, falls back to ``mae_abs`` / ``risk_abs``).
    """
    if position is None:
        return None
    mae_r = getattr(position, "mae_r", None)
    if mae_r is None:
        mae_abs = getattr(position, "mae_abs", None)
        risk_abs = getattr(position, "risk_abs", None)
        if mae_abs is None or risk_abs is None or float(risk_abs or 0) <= 0:
            return None
        try:
            mae_r = float(mae_abs) / float(risk_abs)
        except (TypeError, ValueError):
            return None
    try:
        if float(mae_r) >= 1.0:
            return "tier3_mae_one_r"
    except (TypeError, ValueError):
        return None
    return None


def evaluate_tier4_earnings_t1(
    *,
    days_to_earnings: int | None,
) -> str | None:
    """Tier-4: earnings within one trading day."""
    if days_to_earnings is None:
        return None
    try:
        if int(days_to_earnings) == 1:
            return "tier4_earnings_t1"
    except (TypeError, ValueError):
        return None
    return None


def evaluate_tier4_exdiv_today(
    *,
    is_exdiv_today: bool,
) -> str | None:
    """Tier-4: today is ex-dividend day."""
    return "tier4_exdiv_today" if bool(is_exdiv_today) else None


# ---------------------------------------------------------------------------
# Time-of-day gate
# ---------------------------------------------------------------------------


def _time_of_day_factor(now_utc: _dt.datetime) -> float | None:
    """Return the multiplier to apply to Tier-1 thresholds.

    * 09:30:00–09:35:00 ET → ``None`` (alerts off, every move is
      "extreme" in the opening melt-up).
    * 09:35:00–10:00:00 ET → ``2.0`` (tighten Tier-1; require 2×
      the configured threshold).
    * 10:00:00–16:00:00 ET → ``1.0`` (defaults).
    * Outside RTH → ``1.0`` (defaults, but Tier-1 evaluators read
      regular-session bars so they no-op anyway).
    """
    try:
        local = now_utc.astimezone(_ET)
    except Exception:  # pragma: no cover - tz lib brittle
        return 1.0
    h, m = local.hour, local.minute
    # 09:30–09:35
    if h == 9 and 30 <= m < 35:
        return None
    # 09:35–10:00
    if h == 9 and 35 <= m < 60:
        return 2.0
    return 1.0


# ---------------------------------------------------------------------------
# Audio (winsound, Windows-only; portable no-op everywhere else)
# ---------------------------------------------------------------------------


def _play_chime() -> None:
    """Play a single short chime. Silent on non-Windows."""
    try:  # pragma: no cover - platform-dep
        import winsound  # type: ignore
        winsound.PlaySound(
            "SystemAsterisk",
            winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception:  # pragma: no cover - never crash on audio
        pass


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AlertEngine:
    """Per-panel alert orchestrator.

    Owns:

    * Per-card prior-tick state (PMH/PML break edge detection,
      previous unrealized P&L).
    * The global audio rate-limiter (sliding 10-second window
      with a 2-chime cap; Tier-3 bypasses the cap).
    * The time-of-day gate.

    Lifecycle: one engine per :class:`ChartStackPanel`. Reset on
    sandbox start / auto-cycle via :meth:`reset`.
    """

    _RATE_LIMIT_WINDOW_SECONDS = 10.0
    _RATE_LIMIT_MAX_CHIMES = 2

    def __init__(
        self,
        *,
        clock: Callable[[], _dt.datetime] | None = None,
        play_chime: Callable[[], None] | None = None,
    ) -> None:
        # ``clock`` returns a tz-aware UTC datetime. Default uses
        # ``datetime.now(timezone.utc)``; tests inject a frozen
        # clock for deterministic rate-limit behavior.
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        # ``play_chime`` is monkeypatched in tests to count calls.
        self._play_chime = play_chime or _play_chime
        self._chime_times: list[float] = []
        self._prev_pmh_break: dict[int, bool] = {}
        self._prev_pml_break: dict[int, bool] = {}
        self._prev_unrealized: dict[int, float] = {}
        # Tier-3 persistent ping: per-slot last-ping timestamp so
        # one Tier-3 fires twice 5 s apart but doesn't pile up.
        self._tier3_last_ping: dict[int, float] = {}

    # -- public API ----------------------------------------------------

    def reset(self, slot_index: int | None = None) -> None:
        """Clear per-card / global state.

        ``slot_index`` clears just one slot (used on binding swap);
        ``None`` wipes everything (used on sandbox start /
        auto-cycle).
        """
        if slot_index is None:
            self._chime_times.clear()
            self._prev_pmh_break.clear()
            self._prev_pml_break.clear()
            self._prev_unrealized.clear()
            self._tier3_last_ping.clear()
            return
        self._prev_pmh_break.pop(slot_index, None)
        self._prev_pml_break.pop(slot_index, None)
        self._prev_unrealized.pop(slot_index, None)
        self._tier3_last_ping.pop(slot_index, None)

    def evaluate(
        self,
        slot_index: int,
        *,
        bars: Sequence[Any] = (),
        interval_minutes: int = 5,
        position: Any = None,
        scanner_row: Any = None,
        days_to_earnings: int | None = None,
        is_exdiv_today: bool = False,
        now_utc: _dt.datetime | None = None,
    ) -> AlertResult:
        """Evaluate all four tiers for one card and play any chimes.

        Returns the highest-severity :class:`AlertResult` (or
        :data:`_NO_ALERT` when nothing fired). The result drives
        the card's tint + header badge.
        """
        now = now_utc if now_utc is not None else self._clock()
        rule_ids: list[str] = []
        max_tier = AlertTier.NONE
        badge: str | None = None

        # --- Tier 4 (badge-only; always evaluated, no audio) ------
        for rid in (
            evaluate_tier4_earnings_t1(days_to_earnings=days_to_earnings),
            evaluate_tier4_exdiv_today(is_exdiv_today=is_exdiv_today),
        ):
            if rid is not None:
                rule_ids.append(rid)
                if max_tier.value < AlertTier.TIER_4_YELLOW.value:
                    max_tier = AlertTier.TIER_4_YELLOW
                badge = "T-1" if rid == "tier4_earnings_t1" else "EX-DIV"

        # --- Tier 1 (visual only; ToD-gated thresholds) ----------
        factor = _time_of_day_factor(now)
        if factor is not None and len(bars) >= 15:
            rvol_1m_t = float(_adapter.get("chartstack.alerts.rvol_1m")) * factor
            rvol_5m_t = float(_adapter.get("chartstack.alerts.rvol_5m")) * factor
            atr_t = float(_adapter.get("chartstack.alerts.atr_expansion")) * factor
            for rid in (
                evaluate_tier1_rvol_spike(
                    bars,
                    interval_minutes=interval_minutes,
                    rvol_1m_threshold=rvol_1m_t,
                    rvol_5m_threshold=rvol_5m_t,
                ),
                evaluate_tier1_atr_expansion(bars, atr_threshold=atr_t),
            ):
                if rid is not None:
                    rule_ids.append(rid)
                    if max_tier.value < AlertTier.TIER_1_AMBER.value:
                        max_tier = AlertTier.TIER_1_AMBER

        # --- Tier 2 (visual + single chime; edge-triggered) ------
        pmh_pml_rule = evaluate_tier2_pmh_pml_break(bars)
        if pmh_pml_rule is not None:
            edge_key = "_prev_pmh_break" if pmh_pml_rule == "tier2_pmh_break" else "_prev_pml_break"
            prev_d = getattr(self, edge_key)
            was = bool(prev_d.get(slot_index, False))
            prev_d[slot_index] = True
            if not was:
                rule_ids.append(pmh_pml_rule)
                if max_tier.value < AlertTier.TIER_2_BLUE.value:
                    max_tier = AlertTier.TIER_2_BLUE
                self._try_chime(tier3_bypass=False, now=now)
        else:
            # Clear the edge state when the symbol is no longer
            # above PMH / below PML so a re-cross re-fires.
            self._prev_pmh_break.pop(slot_index, None)
            self._prev_pml_break.pop(slot_index, None)

        new_edge_rule = evaluate_tier2_new_scanner_edge(scanner_row)
        if new_edge_rule is not None:
            rule_ids.append(new_edge_rule)
            if max_tier.value < AlertTier.TIER_2_BLUE.value:
                max_tier = AlertTier.TIER_2_BLUE
            self._try_chime(tier3_bypass=False, now=now)

        # --- Tier 3 (visual + persistent ping every 5s) ---------
        tier3_fired = False
        if position is not None:
            for rid in (
                evaluate_tier3_stop_proximity(
                    bars, position=position, atr_window=0.3),
                evaluate_tier3_pnl_zero_cross(
                    position=position,
                    prev_unrealized=self._prev_unrealized.get(slot_index),
                ),
                evaluate_tier3_mae_one_r(position=position),
            ):
                if rid is not None:
                    rule_ids.append(rid)
                    tier3_fired = True
            # Update prev_unrealized AFTER zero-cross check.
            cur_pnl = getattr(position, "unrealized_pnl", None)
            if cur_pnl is not None:
                try:
                    self._prev_unrealized[slot_index] = float(cur_pnl)
                except (TypeError, ValueError):
                    pass
        if tier3_fired:
            if max_tier.value < AlertTier.TIER_3_RED.value:
                max_tier = AlertTier.TIER_3_RED
            # Persistent ping: chime if 5s elapsed since last
            # tier-3 ping on this slot. Tier-3 bypasses the
            # global rate limit but not the per-slot pacing.
            now_s = now.timestamp()
            last = self._tier3_last_ping.get(slot_index)
            if last is None or (now_s - last) >= 5.0:
                self._tier3_last_ping[slot_index] = now_s
                # Two chimes (double-ping) spaced visually by the
                # natural decay of the system sound. We just play
                # them back to back; the rate-limit bypass means
                # both go through.
                self._try_chime(tier3_bypass=True, now=now)
                self._try_chime(tier3_bypass=True, now=now)
        else:
            self._tier3_last_ping.pop(slot_index, None)

        if max_tier is AlertTier.NONE:
            return _NO_ALERT
        return AlertResult(
            tier=max_tier,
            rule_ids=tuple(rule_ids),
            badge=badge,
        )

    # -- internals -----------------------------------------------------

    def _try_chime(self, *, tier3_bypass: bool, now: _dt.datetime) -> bool:
        """Honor the mute switch and the rate limit; play if allowed."""
        if bool(_adapter.get("chartstack.alerts.audio_muted")):
            return False
        now_s = now.timestamp()
        # Trim the window.
        cutoff = now_s - self._RATE_LIMIT_WINDOW_SECONDS
        self._chime_times = [t for t in self._chime_times if t > cutoff]
        if (
            not tier3_bypass
            and len(self._chime_times) >= self._RATE_LIMIT_MAX_CHIMES
        ):
            return False
        self._chime_times.append(now_s)
        try:
            self._play_chime()
        except Exception:  # noqa: BLE001 - never let chime break a tick
            _LOG.exception("ChartStack chime raised")
        return True


__all__ = [
    "AlertEngine",
    "AlertResult",
    "AlertTier",
    "evaluate_tier1_atr_expansion",
    "evaluate_tier1_rvol_spike",
    "evaluate_tier2_new_scanner_edge",
    "evaluate_tier2_pmh_pml_break",
    "evaluate_tier3_mae_one_r",
    "evaluate_tier3_pnl_zero_cross",
    "evaluate_tier3_stop_proximity",
    "evaluate_tier4_earnings_t1",
    "evaluate_tier4_exdiv_today",
]
