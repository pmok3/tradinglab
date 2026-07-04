"""Persistent window-geometry + sash-position store.

Tk's geometry strings (``WxH+X+Y``) and ``ttk.PanedWindow`` sash
positions are user-perceptible state: if a trader drags the main
window to their right monitor and resizes the chart-vs-watchlist
split just-so, the next launch had better come back the same way.
The existing ``settings.py`` module is a poor fit because it is
opt-in (users explicitly hit "Save Configuration"); window
geometry, by contrast, has to auto-persist silently as the user
moves things around.

This module owns the ``geometry.json`` file under
:func:`tradinglab.paths.app_data_dir` and exposes
:class:`GeometryStore` plus a process-wide :func:`store` singleton.
The store is multi-monitor safe: :func:`_clamp_to_screen` rejects
restores that would land off-screen (e.g. an external monitor that
is no longer plugged in) and falls back to a sensible default.

Persistence is debounced — every ``<Configure>`` event resets a
500 ms timer, and only the trailing event triggers a write — so
rapid window drags don't spam the disk.

Permission errors on save are logged to ``stderr`` and swallowed.
Geometry is convenience, not data integrity; we never want a
read-only filesystem to crash the launch.

The ``TRADINGLAB_GEOMETRY_PATH`` environment variable overrides
the on-disk path (mirrors :mod:`disk_cache`'s
``TRADINGLAB_CACHE_DIR`` pattern) so the smoke harness can keep
its assertions hermetic.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.io_helpers import atomic_write_json

if TYPE_CHECKING:  # pragma: no cover - typing only
    import tkinter as tk
    from tkinter import ttk


SCHEMA_VERSION = 1
_DEBOUNCE_MS = 500
_DEFAULT_GEOMETRY = "1280x800+100+100"

_GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$")
#: Size-only ``WxH`` form (no ``+X+Y`` position). Every dialog passes its
#: ``default_geometry`` in this form (e.g. ``"560x780"``); ``_fallback_geometry``
#: synthesizes a position so the intended size is honored instead of silently
#: falling back to the much larger ``_DEFAULT_GEOMETRY``.
_SIZE_ONLY_RE = re.compile(r"^(\d+)x(\d+)$")
#: Default position appended to a size-only default.
_DEFAULT_POSITION = "+100+100"


def _resolve_default_path() -> Path:
    """Return the on-disk path for ``geometry.json``.

    Honors ``TRADINGLAB_GEOMETRY_PATH`` for test isolation; falls
    back to ``app_data_dir() / "geometry.json"``.
    """
    override = os.environ.get("TRADINGLAB_GEOMETRY_PATH")
    if override:
        return Path(override)
    from ..paths import app_data_dir
    return app_data_dir() / "geometry.json"


def _parse_geometry(geometry: str) -> tuple[int, int, int, int] | None:
    """Parse ``WxH+X+Y`` (or ``WxH-X-Y``) into ``(w, h, x, y)`` or ``None``."""
    if not isinstance(geometry, str):
        return None
    m = _GEOMETRY_RE.match(geometry.strip())
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    except (TypeError, ValueError):
        return None


def _fallback_geometry(default: str) -> str:
    """Normalize a caller ``default`` to a full ``WxH+X+Y`` geometry.

    Dialogs pass their ``default_geometry`` as a size-only ``WxH`` string
    (e.g. ``"560x780"``). ``_parse_geometry`` requires a position, so a
    size-only default would previously be rejected and replaced by the
    much larger module ``_DEFAULT_GEOMETRY`` — making every dialog open at
    1280x800 on a fresh geometry key. Honor the intended size by
    synthesizing a default position for size-only defaults. Only a string
    with no parseable ``WxH`` at all falls back to ``_DEFAULT_GEOMETRY``.
    """
    if _parse_geometry(default):
        return default
    if isinstance(default, str):
        m = _SIZE_ONLY_RE.match(default.strip())
        if m:
            return f"{int(m.group(1))}x{int(m.group(2))}{_DEFAULT_POSITION}"
    return _DEFAULT_GEOMETRY


def compute_screen_percent_geometry(
    screen_w: int,
    screen_h: int,
    *,
    width_pct: float = 0.9,
    height_pct: float = 0.9,
    min_width: int = 1200,
    min_height: int = 780,
    taskbar_buffer_px: int = 80,
) -> str:
    """Return centered ``WxH+X+Y`` using hardcoded screen percentages."""
    try:
        sw = max(1, int(screen_w))
        sh = max(1, int(screen_h))
        width_frac = min(1.0, max(0.1, float(width_pct)))
        height_frac = min(1.0, max(0.1, float(height_pct)))
        min_w = max(1, int(min_width))
        min_h = max(1, int(min_height))
        buffer_px = max(0, int(taskbar_buffer_px))
    except (TypeError, ValueError):
        sw, sh = 1600, 900
        width_frac = height_frac = 0.9
        min_w, min_h, buffer_px = 1200, 780, 80

    max_w = sw
    max_h = sh - buffer_px if sh > min_h + buffer_px else sh
    max_h = max(1, max_h)
    win_w = min(max_w, max(min_w, int(sw * width_frac)))
    win_h = min(max_h, max(min_h, int(sh * height_frac)))
    off_x = max(0, (sw - win_w) // 2)
    off_y = max(0, (sh - win_h) // 3)
    return f"{win_w}x{win_h}+{off_x}+{off_y}"


def _clamp_to_screen(
    geometry: str,
    screen_w: int,
    screen_h: int,
    *,
    default: str = _DEFAULT_GEOMETRY,
    min_size: tuple[int, int] | None = None,
) -> str:
    """Reject restores that would land mostly off-screen or too small.

    A geometry is acceptable iff the top-left is at most 100 px
    outside the virtual screen and the bottom-right is at most
    100 px past the far edge. Anything more aggressive (a window
    saved on a now-disconnected monitor) falls back to ``default``.
    Callers may also pass ``min_size`` to reject stale windows that
    would reopen below the current usability floor.
    """
    fallback = _fallback_geometry(default)
    parsed = _parse_geometry(geometry)
    if parsed is None:
        return fallback
    try:
        screen_w = max(1, int(screen_w))
        screen_h = max(1, int(screen_h))
    except (TypeError, ValueError):
        screen_w, screen_h = 1920, 1080
    w, h, x, y = parsed
    if w <= 0 or h <= 0:
        return fallback
    if min_size is not None:
        try:
            min_w, min_h = int(min_size[0]), int(min_size[1])
        except (TypeError, ValueError, IndexError):
            min_w, min_h = 0, 0
        if (min_w > 0 and w < min_w) or (min_h > 0 and h < min_h):
            return fallback
    if x < -100 or y < -100:
        return fallback
    if x + w > screen_w + 100 or y + h > screen_h + 100:
        return fallback
    return geometry


class GeometryStore:
    """In-memory cache + JSON-backed persistence for Tk geometry.

    See module docstring for the persistence model. Public API is
    locked — see ``geometry_store.spec.md``.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else _resolve_default_path()
        self._windows: dict[str, str] = {}
        self._sashes: dict[str, list[int]] = {}
        self._kv: dict[str, Any] = {}
        self._dirty = False
        self._loaded = False
        # Per-widget after-IDs for debouncing. Keyed by widget id() so we
        # never collide between two widgets sharing the same logical
        # geometry key (shouldn't happen in practice, but cheap to be
        # safe).
        self._pending_after: dict[int, str] = {}

    # ----------------------------------------------------------------- I/O --
    def load(self) -> None:
        """Read ``geometry.json`` into memory; tolerate missing/corrupt files."""
        from ..core.io_helpers import read_json
        self._loaded = True
        payload = read_json(self._path, default=None)
        if payload is None:
            return
        if not isinstance(payload, dict):
            return
        version = payload.get("version")
        # Treat unknown future schemas as missing — never crash the launch.
        if version != SCHEMA_VERSION:
            return
        windows = payload.get("windows", {})
        sashes = payload.get("sashes", {})
        kv = payload.get("kv", {})
        if isinstance(windows, dict):
            self._windows = {
                str(k): str(v) for k, v in windows.items() if isinstance(v, str)
            }
        if isinstance(sashes, dict):
            cleaned: dict[str, list[int]] = {}
            for k, v in sashes.items():
                if isinstance(v, list) and all(isinstance(n, int) for n in v):
                    cleaned[str(k)] = list(v)
            self._sashes = cleaned
        if isinstance(kv, dict):
            self._kv = dict(kv)
        self._dirty = False

    def save(self) -> None:
        """Atomic write; logs + swallows permission errors."""
        payload = {
            "version": SCHEMA_VERSION,
            "windows": dict(self._windows),
            "sashes": {k: list(v) for k, v in self._sashes.items()},
            "kv": dict(self._kv),
        }
        try:
            atomic_write_json(self._path, payload, sort_keys=True)
            self._dirty = False
        except OSError as exc:
            print(
                f"[geometry_store] save to {self._path} failed: {exc}",
                file=sys.stderr,
            )

    # ----------------------------------------------------------- KV access --
    def get(self, key: str, default: Any = None) -> Any:
        """Return a free-form value previously stored via :meth:`set`."""
        return self._kv.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store a free-form value and schedule a debounced save."""
        self._kv[key] = value
        self._dirty = True
        # KV mutations don't carry a Tk widget context for debouncing;
        # write-through synchronously. Callers concerned about disk churn
        # should batch via :meth:`save` directly.
        self.save()

    # --------------------------------------------------------- raw windows --
    def get_window(self, key: str) -> str | None:
        """Return the stored geometry string for ``key`` (or ``None``)."""
        return self._windows.get(key)

    def set_window(self, key: str, geometry: str) -> None:
        """Store a geometry string and mark dirty (no debounce)."""
        if not isinstance(geometry, str):
            return
        if self._windows.get(key) == geometry:
            return
        self._windows[key] = geometry
        self._dirty = True

    def get_window_size(self, key: str) -> tuple[int, int] | None:
        raw = self._kv.get(f"window_size.{key}")
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        try:
            width = int(raw[0])
            height = int(raw[1])
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return width, height

    def set_window_size(self, key: str, width: int, height: int) -> None:
        """Store size only, preserving caller/default position on restore."""
        try:
            size = [max(1, int(width)), max(1, int(height))]
        except (TypeError, ValueError):
            return
        k = f"window_size.{key}"
        if self._kv.get(k) == size:
            return
        self._kv[k] = size
        self._dirty = True
        self.save()

    def clear_window_size(self, key: str) -> None:
        k = f"window_size.{key}"
        if k in self._kv:
            del self._kv[k]
            self._dirty = True
            self.save()

    def get_sash(self, key: str) -> list[int] | None:
        """Return the stored sash positions for ``key`` (or ``None``)."""
        v = self._sashes.get(key)
        return list(v) if v is not None else None

    def set_sash(self, key: str, positions: list[int]) -> None:
        """Store sash positions and mark dirty (no debounce)."""
        positions = [int(p) for p in positions]
        if self._sashes.get(key) == positions:
            return
        self._sashes[key] = positions
        self._dirty = True

    # ------------------------------------------------------- Tk wiring API --
    def restore_window(
        self,
        toplevel: tk.Misc,
        key: str,
        default: str = _DEFAULT_GEOMETRY,
        *,
        min_size: tuple[int, int] | None = None,
    ) -> str:
        """Apply the stored geometry for ``key`` to ``toplevel``.

        Returns the geometry string actually applied (after the
        multi-monitor clamp). Falls back to ``default`` whenever the
        stored geometry is missing, off-screen, or smaller than
        ``min_size`` when one is supplied.
        """
        if not self._loaded:
            self.load()
        stored = self._windows.get(key, default)
        try:
            screen_w = int(toplevel.winfo_screenwidth())
            screen_h = int(toplevel.winfo_screenheight())
        except Exception:  # noqa: BLE001 - Tk not initialised; fall back
            screen_w, screen_h = 1920, 1080
        applied = _clamp_to_screen(stored, screen_w, screen_h, default=default, min_size=min_size)
        try:
            toplevel.geometry(applied)
        except Exception:  # noqa: BLE001 - geometry rejection is non-fatal
            pass
        return applied

    def restore_window_size(
        self,
        toplevel: tk.Misc,
        key: str,
        default: str = _DEFAULT_GEOMETRY,
        *,
        min_size: tuple[int, int] | None = None,
    ) -> str:
        """Apply stored size for ``key`` while preserving default position."""
        if not self._loaded:
            self.load()
        parsed_default = _parse_geometry(_fallback_geometry(default))
        if parsed_default is None:
            parsed_default = _parse_geometry(_DEFAULT_GEOMETRY)
        assert parsed_default is not None
        _w, _h, x, y = parsed_default
        saved_size = self.get_window_size(key)
        if saved_size is None:
            candidate = _fallback_geometry(default)
        else:
            width, height = saved_size
            candidate = f"{width}x{height}{x:+d}{y:+d}"
        try:
            screen_w = int(toplevel.winfo_screenwidth())
            screen_h = int(toplevel.winfo_screenheight())
        except Exception:  # noqa: BLE001
            screen_w, screen_h = 1920, 1080
        applied = _clamp_to_screen(candidate, screen_w, screen_h, default=default, min_size=min_size)
        try:
            toplevel.geometry(applied)
        except Exception:  # noqa: BLE001
            pass
        return applied

    def bind_window(self, toplevel: tk.Misc, key: str) -> None:
        """Wire ``<Configure>`` so geometry changes auto-persist (debounced)."""

        def _on_configure(_event=None, *, _store=self, _w=toplevel, _k=key) -> None:
            _store._schedule_window_save(_w, _k)

        try:
            toplevel.bind("<Configure>", _on_configure, add="+")
        except Exception:  # noqa: BLE001 - test stubs may not support bind
            pass

    def restore_sash(
        self,
        paned: ttk.PanedWindow,
        key: str,
        default_positions: list[int],
        *,
        min_pane_widths: list[int] | None = None,
    ) -> None:
        """Apply stored sash positions after the paned has rendered.

        ``ttk.PanedWindow.sashpos`` only accepts coordinates inside the
        widget's current size; requesting ``sashpos(0, 967)`` while the
        paned is still 1-pixel-wide (mid-layout) silently clamps to
        ``0`` and collapses the leftmost pane. We therefore *poll*
        with ``after`` until ``winfo_width()`` reports something usable
        (≥ ``max(positions) + 1``), with a hard cap so a never-mapped
        widget can't loop forever.

        ``min_pane_widths`` (optional, one entry per pane in left-to-
        right order) provides a sanity clamp: if applying the *stored*
        positions would leave any pane narrower than its minimum, we
        fall back to ``default_positions``. This guards against a
        previously-saved pathological sash (e.g. user accidentally
        dragged the chart pane to 30 px and quit) silently rendering
        the app unusable on the next startup.
        """
        if not self._loaded:
            self.load()
        stored_positions = self._sashes.get(key)
        positions = list(stored_positions) if stored_positions else list(default_positions)

        if not positions:
            return

        defaults = list(default_positions) if default_positions else list(positions)
        max_pos = max(int(p) for p in positions)
        max_pos_defaults = max(int(p) for p in defaults) if defaults else max_pos
        # Wait until the paned is wide enough for whichever set we
        # might end up applying — picking the smaller set first risks
        # exiting the poll loop before the wider fallback would fit.
        poll_target = max(max_pos, max_pos_defaults)
        # Hard cap on retries: 40 × 25 ms = 1.0 s. After that the
        # paned is either never going to be mapped (test stub /
        # withdrawn root) or something else is keeping it tiny —
        # apply anyway, since silently leaving the user with a
        # collapsed pane is worse than a clamped sash.
        max_attempts = 40

        state = {"attempts": 0}

        def _pane_widths_for(positions_: list[int], width: int) -> list[int]:
            widths: list[int] = []
            prev = 0
            for pos in positions_:
                widths.append(int(pos) - prev)
                prev = int(pos)
            widths.append(max(0, width - prev))
            return widths

        def _violates_min(widths: list[int]) -> bool:
            if not min_pane_widths:
                return False
            for i, w in enumerate(widths):
                if i >= len(min_pane_widths):
                    break
                if w < int(min_pane_widths[i]):
                    return True
            return False

        def _apply() -> None:
            try:
                w = int(paned.winfo_width())
            except Exception:  # noqa: BLE001
                w = 0
            state["attempts"] += 1
            if w <= poll_target and state["attempts"] < max_attempts:
                try:
                    paned.after(25, _apply)
                    return
                except Exception:  # noqa: BLE001
                    pass
            # Decide which positions to apply: stored, unless they would
            # collapse a pane below its declared minimum, in which case
            # we revert to the layout-aware defaults.
            chosen = positions
            if (
                stored_positions
                and min_pane_widths
                and w > 0
                and _violates_min(_pane_widths_for(positions, w))
            ):
                chosen = defaults
            for idx, pos in enumerate(chosen):
                try:
                    paned.sashpos(idx, int(pos))
                except Exception:  # noqa: BLE001 - bad index, ignore
                    pass

        try:
            paned.after_idle(_apply)
        except Exception:  # noqa: BLE001
            _apply()

    def bind_sash(self, paned: ttk.PanedWindow, key: str) -> None:
        """Snapshot sash positions on mouse-release and persist."""

        def _on_release(_event=None, *, _store=self, _p=paned, _k=key) -> None:
            try:
                count = len(_p.panes()) - 1
                positions = [int(_p.sashpos(i)) for i in range(max(0, count))]
            except Exception:  # noqa: BLE001
                return
            if positions:
                _store.set_sash(_k, positions)
                _store.save()

        try:
            paned.bind("<ButtonRelease-1>", _on_release, add="+")
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------- internals --
    def _schedule_window_save(self, widget: tk.Misc, key: str) -> None:
        """Debounce ``<Configure>`` bursts; only the trailing one persists."""
        wid = id(widget)
        prev = self._pending_after.pop(wid, None)
        if prev is not None:
            try:
                widget.after_cancel(prev)
            except Exception:  # noqa: BLE001
                pass

        def _flush(*, _store=self, _w=widget, _k=key, _wid=wid) -> None:
            _store._pending_after.pop(_wid, None)
            try:
                geometry = _w.winfo_geometry()
            except Exception:  # noqa: BLE001
                return
            if not _parse_geometry(geometry):
                return
            _store.set_window(_k, geometry)
            _store.save()

        try:
            after_id = widget.after(_DEBOUNCE_MS, _flush)
            self._pending_after[wid] = after_id
        except Exception:  # noqa: BLE001
            _flush()


# ----------------------------------------------------------- module singleton --
_singleton: GeometryStore | None = None


def store() -> GeometryStore:
    """Return the process-wide :class:`GeometryStore` singleton."""
    global _singleton
    if _singleton is None:
        _singleton = GeometryStore()
        _singleton.load()
    return _singleton


def _reset_singleton_for_tests() -> None:
    """Drop the cached singleton. Test-only seam."""
    global _singleton
    _singleton = None


__all__ = [
    "GeometryStore",
    "SCHEMA_VERSION",
    "store",
    "attach_persistent_geometry",
    "compute_screen_percent_geometry",
    "_clamp_to_screen",
    "_reset_singleton_for_tests",
]


def attach_persistent_geometry(
    toplevel: tk.Misc,
    key: str,
    default: str = _DEFAULT_GEOMETRY,
) -> None:
    """Convenience: restore + bind window geometry via the module singleton.

    Equivalent to::

        gs = store()
        gs.restore_window(toplevel, key, default)
        gs.bind_window(toplevel, key)

    Use from any ``tk.Toplevel.__init__`` that doesn't inherit from
    :class:`BaseModalDialog` (which handles this automatically). The
    call is wrapped in a try/except so a broken store can never
    prevent the dialog from opening — geometry persistence is
    convenience, not a hard dependency.
    """
    try:
        gs = store()
        gs.restore_window(toplevel, key, default)
        gs.bind_window(toplevel, key)
    except Exception:  # noqa: BLE001
        pass
