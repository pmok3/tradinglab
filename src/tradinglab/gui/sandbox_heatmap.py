"""Sandbox heatmap pop-out window.

Non-modal ``tk.Toplevel`` launched from the Sandbox menu while a replay
session is active. Renders the S&P 500 as a Finviz-style sector ->
industry treemap (matplotlib ``Rectangle`` patches on an embedded
``FigureCanvasTkAgg``), colored by 1-Day % change **as of the replay
clock** and sized by historically-scaled market cap. Recolors every
tick; relays out per session; click a tile to load that symbol on the
primary chart. Blind-mode-safe and dark-mode-themed.

See ``gui/sandbox_heatmap.spec.md`` and ``docs/SANDBOX_HEATMAP.md``.
"""

from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable
from datetime import datetime, timezone
from tkinter import ttk
from typing import Any

from ..backtest.heatmap import (
    HeatmapTile,
    apply_colors,
    build_layout,
    compute_1d_pct,
    members_asof,
    scaled_cap,
    text_color_for,
)
from ..backtest.heatmap_provider import HeatmapProvider
from .native_theme import apply_toplevel_theme, current_theme

# A price source resolves ``(price_at_clock, prior_close)`` for a symbol.
PriceSource = Callable[[str, int], "tuple[float | None, float | None]"]

# Minimum normalized tile side (of the unit square) to draw a text label.
_LABEL_W = 0.045
_LABEL_H = 0.030
_FULL_LABEL_H = 0.050  # show ticker + % above this height, else ticker only
_SECTOR_HDR_W = 0.06
_SECTOR_HDR_H = 0.05


# ---------------------------------------------------------------------------
# Pure helpers (headless-testable)
# ---------------------------------------------------------------------------


def tile_at(
    tiles: tuple[HeatmapTile, ...], x: float | None, y: float | None
) -> HeatmapTile | None:
    """Return the tile containing point ``(x, y)`` in the unit square."""
    if x is None or y is None:
        return None
    for t in tiles:
        if t.w > 0.0 and t.h > 0.0 and t.x <= x <= t.x + t.w and t.y <= y <= t.y + t.h:
            return t
    return None


def compute_size_pct(
    provider: HeatmapProvider,
    price_source: PriceSource,
    members: list[str],
    clock: int,
    *,
    shares_at: Callable[[str, int], tuple[float | None, bool]] | None = None,
) -> tuple[dict[str, float], dict[str, float | None], set[str]]:
    """Compute ``(size_by_symbol, pct_by_symbol, approx_symbols)``.

    ``size`` is split-consistent historically-scaled cap
    (``shares × price``); ``pct`` is 1-Day % (price-at-clock vs prior
    close). Symbols whose shares came from a carried-back count (or are
    missing / not-yet-primed) land in ``approx_symbols``. ``shares_at``
    defaults to ``provider.shares_at`` (fetching); the window passes
    ``provider.peek_shares_at`` for a non-blocking render.
    """
    sa = shares_at or provider.shares_at
    size_by: dict[str, float] = {}
    pct_by: dict[str, float | None] = {}
    approx: set[str] = set()
    for sym in members:
        price, prior = price_source(sym, clock)
        pct_by[sym] = compute_1d_pct(price, prior)
        shares, is_approx = sa(sym, clock)
        size_by[sym] = scaled_cap(shares, price)
        if is_approx or shares is None:
            approx.add(sym)
    return size_by, pct_by, approx


def _default_price_source(
    symbol: str, clock_ts: int, *, source: str = "yfinance", interval: str = "1d"
) -> tuple[float | None, float | None]:
    """Daily-bar price source: last close ≤ clock + the prior close.

    Reads the disk cache (populated by the sandbox "Download Replay
    Data…" preload). Best-effort — any failure yields ``(None, None)``.
    """
    try:
        from .. import disk_cache

        bars = disk_cache.load(source, symbol, interval) or []
    except Exception:
        return (None, None)
    cutoff = float(clock_ts)
    idx = -1
    for i, c in enumerate(bars):
        try:
            cts = c.date.timestamp()
        except (AttributeError, ValueError, OverflowError, OSError):
            continue
        if cts <= cutoff:
            idx = i
        else:
            break
    if idx < 0:
        return (None, None)

    def _finite(v: Any) -> float | None:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if f != f else f

    price = _finite(bars[idx].close)
    prior = _finite(bars[idx - 1].close) if idx - 1 >= 0 else None
    return (price, prior)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


class SandboxHeatmapWindow(tk.Toplevel):
    """Pop-out Finviz-style heatmap driven by the sandbox replay clock."""

    def __init__(
        self,
        app: Any,
        controller: Any,
        *,
        provider: HeatmapProvider | None = None,
        price_source: PriceSource | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(app, **kwargs)
        self.app = app
        self.controller = controller
        self.provider = provider if provider is not None else HeatmapProvider()
        self.price_source = price_source or _default_price_source
        self.title("Market Heatmap")

        self._layout = None
        self._tiles: tuple[HeatmapTile, ...] = ()
        self._last_session_date: Any = None
        self._cid_motion: int | None = None
        self._cid_click: int | None = None
        self._primed = False
        self._priming = False
        self._prime_done = False

        self._header = ttk.Label(self, text="Market Heatmap", anchor="w")
        self._header.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(6, 2))

        self._build_canvas()

        self._status = ttk.Label(self, text="Hover a tile…", anchor="w")
        self._status.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(2, 4))
        self._footer = ttk.Label(
            self,
            text=(
                "Point-in-time S&P 500 (look-ahead removed). Sizes: "
                "historical market cap; hatched = approximate."
            ),
            anchor="w",
            font=("TkDefaultFont", 8),
        )
        self._footer.pack(side=tk.BOTTOM, fill=tk.X, padx=6)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_alive = True
        self._last_polled_clock: int | None = None
        self.refresh()
        self._last_polled_clock = self._clock()
        self.after(250, self._poll_clock)

    # -- construction --

    def _build_canvas(self) -> None:
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        self._fig = Figure(figsize=(9.5, 7.0), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._cid_motion = self._canvas.mpl_connect("motion_notify_event", self._on_motion)
        self._cid_click = self._canvas.mpl_connect("button_press_event", self._on_click)

    # -- data --

    def _clock(self) -> int | None:
        try:
            return self.controller.clock_ts()
        except Exception:
            return None

    def _blind(self) -> bool:
        return bool(getattr(self.controller, "blind", False))

    def _members(self, clock: int) -> list[str]:
        return list(members_asof(self.provider.date_added(), clock))

    def _rebuild_layout(self, clock: int, members: list[str]) -> dict[str, float | None]:
        size_by, pct_by, approx = compute_size_pct(
            self.provider,
            self.price_source,
            members,
            clock,
            shares_at=self.provider.peek_shares_at,
        )
        self._layout = build_layout(
            symbols=members,
            size_by_symbol=size_by,
            classification=self.provider.classification(),
            approx_size_symbols=approx,
        )
        return pct_by

    def _pcts_only(self, clock: int) -> dict[str, float | None]:
        pct_by: dict[str, float | None] = {}
        if self._layout is None:
            return pct_by
        for t in self._layout.tiles:
            price, prior = self.price_source(t.symbol, clock)
            pct_by[t.symbol] = compute_1d_pct(price, prior)
        return pct_by

    def refresh(self) -> None:
        """Full rebuild + recolor (used on open and universe change)."""
        clock = self._clock()
        if clock is None:
            self._render_empty("Sandbox clock not started.")
            return
        members = self._members(clock)
        pct_by = self._rebuild_layout(clock, members)
        self._last_session_date = self._session_date()
        self._recolor(clock, pct_by)
        if not self._primed and not self._priming:
            self._start_prime(members)

    def on_replay_tick(self) -> None:
        """Recolor from the controller; relayout first if the session rolled."""
        clock = self._clock()
        if clock is None:
            return
        session = self._session_date()
        if session != self._last_session_date or self._layout is None:
            self._last_session_date = session
            pct_by = self._rebuild_layout(clock, self._members(clock))
        else:
            pct_by = self._pcts_only(clock)
        self._recolor(clock, pct_by)

    def _session_date(self) -> Any:
        fn = getattr(self.controller, "current_session_date", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                return None
        return None

    def _start_prime(self, members: list[str]) -> None:
        """Fetch the universe's shares on a daemon thread; refresh when done.

        Renders complete instantly with approximate (cache-only) sizes;
        the background prime fills `get_shares_full` for every member
        (cached to disk), then a poll on the Tk thread triggers a full
        refresh so real cap sizes appear. Uses the result-flag + `after`
        poll pattern (never cross-thread `after`), per CLAUDE.md §7.15.
        """
        if self._priming or self._primed:
            return
        self._priming = True
        self._prime_done = False
        syms = list(members)

        def _work() -> None:
            try:
                self.provider.prime(syms)
            except Exception:
                pass
            self._prime_done = True

        threading.Thread(target=_work, daemon=True, name="HeatmapSharesPrime").start()
        try:
            self.after(300, self._poll_prime)
        except tk.TclError:
            pass

    def _poll_prime(self) -> None:
        if self._prime_done:
            self._priming = False
            self._primed = True
            try:
                self.refresh()
            except Exception:
                pass
            return
        try:
            self.after(300, self._poll_prime)
        except tk.TclError:
            pass

    def _poll_clock(self) -> None:
        """Cheap clock poller: refresh when the replay clock advances.

        Decouples the window from the controller / panel tick path — it
        self-updates while open, so no subscriber wiring is needed.
        """
        if not getattr(self, "_poll_alive", False):
            return
        cur = self._clock()
        if cur is not None and cur != self._last_polled_clock:
            self._last_polled_clock = cur
            try:
                self.on_replay_tick()
            except Exception:
                pass
        try:
            self.after(250, self._poll_clock)
        except tk.TclError:
            self._poll_alive = False

    # -- render --

    def _recolor(self, clock: int, pct_by: dict[str, float | None]) -> None:
        if self._layout is None:
            return
        model = apply_colors(
            self._layout, pct_by_symbol=pct_by, as_of_ts=int(clock), universe_id="sp500"
        )
        self._tiles = model.tiles
        self._draw(model, clock)

    def _draw(self, model: Any, clock: int) -> None:
        theme = current_theme(self.app if self.app is not None else self)
        bg = theme.get("win_bg", "#ffffff")
        hdr_fg = theme.get("text", "#000000")
        apply_toplevel_theme(self, theme)

        ax = self._ax
        ax.clear()
        self._fig.set_facecolor(bg)
        ax.set_facecolor(bg)
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
        ax.axis("off")

        from matplotlib.patches import Rectangle

        focus = getattr(self.controller, "focus_symbol", None)
        positions = self._positions()

        for t in model.tiles:
            rect = Rectangle(
                (t.x, t.y), t.w, t.h, facecolor=t.fill, edgecolor=bg, linewidth=0.4
            )
            if t.approx_size:
                rect.set_hatch("//")
            if focus and t.symbol == focus:
                rect.set_edgecolor("#ffd24d")
                rect.set_linewidth(2.0)
            ax.add_patch(rect)

            if t.w >= _LABEL_W and t.h >= _LABEL_H:
                label = t.symbol
                if t.h >= _FULL_LABEL_H and t.pct is not None:
                    label = f"{t.symbol}\n{t.pct:+.1f}%"
                ax.text(
                    t.x + t.w / 2.0,
                    t.y + t.h / 2.0,
                    label,
                    ha="center",
                    va="center",
                    fontsize=7,
                    color=text_color_for(t.fill),
                )
            badge = positions.get(t.symbol)
            if badge:
                ax.text(
                    t.x + t.w - 0.004,
                    t.y + t.h - 0.004,
                    badge,
                    ha="right",
                    va="top",
                    fontsize=7,
                    fontweight="bold",
                    color="#ffffff",
                )

        for sector, (sx, sy, sw, sh) in self._layout.sector_bounds.items():
            if sw >= _SECTOR_HDR_W and sh >= _SECTOR_HDR_H:
                ax.text(
                    sx + 0.003,
                    sy + sh - 0.003,
                    sector.upper(),
                    ha="left",
                    va="top",
                    fontsize=7,
                    fontweight="bold",
                    color=hdr_fg,
                    alpha=0.85,
                )

        self._header.configure(text=self._title_text(clock, len(model.tiles)))
        self._canvas.draw_idle()

    def _render_empty(self, msg: str) -> None:
        theme = current_theme(self.app if self.app is not None else self)
        bg = theme.get("win_bg", "#ffffff")
        apply_toplevel_theme(self, theme)
        ax = self._ax
        ax.clear()
        self._fig.set_facecolor(bg)
        ax.set_facecolor(bg)
        ax.axis("off")
        ax.text(0.5, 0.5, msg, ha="center", va="center",
                color=theme.get("text", "#000000"))
        self._canvas.draw_idle()

    def _positions(self) -> dict[str, str]:
        out: dict[str, str] = {}
        fn = getattr(self.controller, "positions_snapshot", None)
        if not callable(fn):
            return out
        try:
            for p in fn():
                qty = float(p.get("quantity", 0.0))
                if qty:
                    out[p["symbol"]] = "L" if qty > 0 else "S"
        except Exception:
            return {}
        return out

    def _title_text(self, clock: int, n_tiles: int) -> str:
        if self._blind():
            bar = self._blind_bar_label()
            return f"Market Heatmap — {bar} · {n_tiles} names · 1 Day %"
        stamp = self._fmt_clock(clock)
        return f"Market Heatmap — {stamp} · {n_tiles} names · 1 Day %"

    def _blind_bar_label(self) -> str:
        idx = None
        eng = getattr(self.controller, "engine", None)
        clk = getattr(eng, "clock", None)
        val = getattr(clk, "index", None)
        if isinstance(val, int) and val >= 0:
            idx = val + 1
        return f"Replay Bar {idx}" if idx is not None else "Replay (blind)"

    @staticmethod
    def _fmt_clock(clock: int) -> str:
        try:
            return datetime.fromtimestamp(int(clock), tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
        except (ValueError, OverflowError, OSError):
            return str(clock)

    # -- interaction --

    def _on_motion(self, event: Any) -> None:
        if event is None or not getattr(event, "inaxes", None):
            return
        t = tile_at(self._tiles, event.xdata, event.ydata)
        if t is None:
            self._status.configure(text="Hover a tile…")
            return
        pct = "n/a" if t.pct is None else f"{t.pct:+.2f}%"
        approx = " (approx size)" if t.approx_size else ""
        self._status.configure(
            text=f"{t.symbol} · {t.sector} / {t.industry} · {pct}{approx}"
        )

    def _on_click(self, event: Any) -> None:
        if event is None or not getattr(event, "inaxes", None):
            return
        t = tile_at(self._tiles, event.xdata, event.ydata)
        if t is not None:
            self._load_on_chart(t.symbol)

    def _load_on_chart(self, symbol: str) -> None:
        # Prefer the app's register-and-focus (loads the symbol into the
        # active sandbox + focuses the primary chart); fall back to the
        # controller's set_focus for already-registered symbols / stubs.
        app_fn = getattr(self.app, "_sandbox_register_and_focus", None)
        if callable(app_fn):
            try:
                if app_fn(symbol):
                    return
            except Exception:
                pass
        fn = getattr(self.controller, "set_focus", None)
        if callable(fn):
            try:
                fn(symbol)
            except Exception:
                pass

    def close(self) -> None:
        self._poll_alive = False
        for cid in (self._cid_motion, self._cid_click):
            try:
                if cid is not None:
                    self._canvas.mpl_disconnect(cid)
            except Exception:
                pass
        self._cid_motion = self._cid_click = None
        try:
            self.destroy()
        except tk.TclError:
            pass


def open_sandbox_heatmap(app: Any, controller: Any, **kwargs: Any) -> SandboxHeatmapWindow | None:
    """Sandbox-menu action — open (or focus) the heatmap window (singleton)."""
    if controller is None or not getattr(controller, "is_active", lambda: False)():
        return None
    existing = getattr(app, "_sandbox_heatmap_win", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.on_replay_tick()
                return existing
        except tk.TclError:
            pass
    win = SandboxHeatmapWindow(app, controller, **kwargs)
    try:
        app._sandbox_heatmap_win = win
    except Exception:
        pass
    return win


__all__ = ("SandboxHeatmapWindow", "open_sandbox_heatmap", "tile_at", "compute_size_pct")
