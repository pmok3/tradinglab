"""Indicator configuration, manager, presets, persistence round-trip.

This module owns the **user-configured** indicator state — separate
from the pure compute classes in :mod:`indicators.moving_averages`,
:mod:`indicators.rsi`, :mod:`indicators.bollinger`. A
:class:`IndicatorConfig` is one configured *instance* (a particular
SMA(20) with particular color + scope + intervals). The
:class:`IndicatorManager` owns the live list, the named presets, and
fires observer callbacks on change.

Design notes
------------

* **Tk-thread ownership.** The manager is mutated only from the Tk
  main thread. No locks. Compute calls (which are pure and may run on
  worker threads) take the indicator instance as a value, not a
  reference into the manager.

* **Snapshot-then-notify.** Observer callbacks are snapshotted to a
  list before iteration, so a callback that adds/removes itself or
  another subscriber doesn't corrupt the list mid-walk.

* **Debounced redraw.** The manager's ``schedule_redraw`` hook lets
  the app coalesce N add/remove/update events in a single tick into
  one redraw via ``Tk.after_idle``. The manager itself doesn't import
  Tk; the app injects the scheduler at construction.

* **Unknown-kind placeholders.** When hydrating a config from a saved
  file whose ``kind_id`` is not currently registered (e.g. a custom
  indicator file is missing or a built-in was renamed), the manager
  stores it as a disabled placeholder — visible in the Manage dialog
  with an "Unknown indicator" label, never executed.

* **kind_version.** Persisted alongside ``kind_id``. Reserved for a
  future per-indicator ``migrate(params, from_version)`` hook; today
  unrecognized versions are loaded as-is and a status-log warning is
  emitted by the caller.
"""

from __future__ import annotations

import builtins
import copy
import itertools
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.thread_guard import require_tk_thread
from ._palette import FALLBACK_GRAY
from .base import (
    _LEGACY_MA_OUTPUT_KEYS,
    _LEGACY_Z_OUTPUT_KIND_IDS,
    LineStyle,
    factory_by_kind_id,
    factory_is_available_for,
    migrate_kind_id,
)

# Allowed render scopes. ``main`` is the primary chart; ``compare`` is
# the second-ticker overlay; ``drilldown`` is the 5m intraday view
# spawned from a 1d double-click.
SCOPES = ("main", "compare", "drilldown")
DEFAULT_SCOPES: frozenset[str] = frozenset({"main", "drilldown"})


# Monotonic id generator for IndicatorConfig instances. Survives
# add/remove churn so observers can correlate events to configs.
_ID_LOCK = threading.Lock()
_ID_COUNTER = itertools.count(1)


def _next_id() -> int:
    with _ID_LOCK:
        return next(_ID_COUNTER)


def _factory_pane_group(kind_id: str) -> str:
    """Look up the ``pane_group`` class attribute on the factory for
    ``kind_id``. Returns "" when no factory or no attribute.

    Used by :meth:`IndicatorConfig.from_dict` to seed the field for
    legacy persisted configs that pre-date the ``pane_group`` field.
    """
    if not kind_id:
        return ""
    entry = factory_by_kind_id(kind_id)
    if entry is None:
        return ""
    _name, factory = entry
    return str(getattr(factory, "pane_group", "") or "")


def effective_pane_group(cfg: IndicatorConfig) -> str:
    """Resolve the runtime pane group for ``cfg``, params-aware.

    Order of resolution:

    1. ``factory.pane_group_for(cfg.params)`` if the factory defines
       it. This wins because the unified RVOL / RRVOL indicators
       toggle between ``"rvol"`` and ``"rvol_z"`` based on the
       ``z_score`` param — the persisted ``cfg.pane_group`` may be
       stale (e.g. user toggled ``z_score`` after add).
    2. ``cfg.pane_group`` — the persisted value (preferred over the
       class attribute so user customisation wins).
    3. ``factory.pane_group`` class attribute (legacy fallback).

    Used by :func:`indicators.render.applicable_pane_groups` to bucket
    configs into shared sub-panes.
    """
    entry = factory_by_kind_id(cfg.kind_id)
    if entry is not None:
        _name, factory = entry
        pg_for = getattr(factory, "pane_group_for", None)
        if callable(pg_for):
            try:
                derived = pg_for(cfg.params)
                if derived:
                    return str(derived)
            except Exception:  # noqa: BLE001
                pass
    return str(getattr(cfg, "pane_group", "") or "") or _factory_pane_group(cfg.kind_id)


# --- Config dataclass --------------------------------------------------------


def _migrate_avwap_anchor_params(params: dict[str, Any]) -> dict[str, Any]:
    """Promote a legacy symbol-blind AVWAP anchor to shared-anchor mode.

    Pre-symbol-keyed AVWAP configs stored a single ``anchor_ts`` that
    applied to every rendered symbol — exactly the new "shared" anchor
    semantics. A legacy config carrying a concrete ``anchor_ts`` and
    NONE of the new keys (``anchors`` / ``shared_anchor_ts`` /
    ``anchor_shared``) is promoted to ``anchor_shared=True`` with
    ``shared_anchor_ts`` set, so the line keeps drawing on every symbol
    exactly as before. A legacy blank/absent anchor stays per-symbol
    (empty) so the indicator reads "Not set" until the user picks one.
    See `indicators/avwap.spec.md` "Symbol-keyed anchors".
    """
    if any(k in params for k in ("anchors", "shared_anchor_ts", "anchor_shared")):
        return params
    legacy = str(params.get("anchor_ts") or "").strip()
    if not legacy:
        return params
    migrated = dict(params)
    migrated["anchor_shared"] = True
    migrated["shared_anchor_ts"] = legacy
    return migrated


@dataclass
class IndicatorConfig:
    """One user-configured indicator instance.

    Fields:
      ``id``           — process-monotonic int (re-issued on hydrate).
      ``kind_id``      — stable indicator id (e.g. ``"sma"``); routes
                         to a factory via :func:`factory_by_kind_id`.
      ``kind_version`` — version of the params schema this config was
                         saved against. Round-trips for future
                         migrations.
      ``display_name`` — user-editable label (default = factory name
                         on the configured params, e.g. ``"SMA(20)"``).
      ``params``       — kwargs for the factory call.
      ``style``        — per-output-key :class:`LineStyle` overrides
                         layered on top of the indicator's
                         ``default_style``.
      ``intervals``    — empty = all; otherwise an explicit set of
                         interval keys (e.g. ``{"1d", "1h"}``).
      ``scopes``       — subset of :data:`SCOPES`; default is
                         :data:`DEFAULT_SCOPES` (``main`` only).
      ``visible``      — master toggle.
      ``origin``       — ``"builtin"`` or ``"custom:<path>"``;
                         informational only.
      ``unknown``      — set when ``kind_id`` was not in the registry
                         at hydrate time. The manager treats unknown
                         configs as disabled placeholders.
    """

    id: int = field(default_factory=_next_id)
    kind_id: str = ""
    kind_version: int = 1
    display_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    style: dict[str, LineStyle] = field(default_factory=dict)
    intervals: tuple[str, ...] = ()
    scopes: frozenset[str] = DEFAULT_SCOPES
    visible: bool = True
    origin: str = "builtin"
    unknown: bool = False
    #: When non-empty, all non-overlay configs sharing this group key
    #: render onto a single shared lower pane (one set of axes, one
    #: set of reference dashes). Empty string keeps the legacy
    #: "one config = one pane" behaviour. Set as a class attribute on
    #: the factory (``pane_group = "rvol"``); the factory value is
    #: copied onto the config at construction time so persistence
    #: round-trips even if the factory's default later changes.
    pane_group: str = ""

    # ---- factory + compute ----

    def make_indicator(self):
        """Instantiate the underlying indicator using ``params``.

        Returns ``None`` for unknown-kind placeholders. Raises
        ``KeyError`` if the kind disappeared between the hydrate-time
        check and this call (caller should treat as unknown).
        """
        if self.unknown:
            return None
        entry = factory_by_kind_id(self.kind_id)
        if entry is None:
            raise KeyError(f"unknown indicator kind_id: {self.kind_id!r}")
        _name, factory = entry
        return factory(**self.params)

    def applies_to(self, scope: str, interval: str) -> bool:
        """True iff this config is visible AND in-scope AND
        interval-eligible AND its factory considers itself available
        on ``interval`` for these specific ``params``.

        Factory-level availability (via ``is_available_for`` or the
        legacy ``available_intervals`` attribute) is consulted last so
        a params-dependent indicator like the unified ``rvol`` with
        ``mode="cumulative"`` is silently filtered out on a daily chart
        even if the user persisted a config with ``intervals=()``
        (= all). Render and the dialog's preview path both flow through
        this method, so they remain consistent.
        """
        if not self.visible or self.unknown:
            return False
        if scope not in self.scopes:
            return False
        if self.intervals and interval not in self.intervals:
            return False
        entry = factory_by_kind_id(self.kind_id)
        if entry is not None:
            _name, factory = entry
            if not factory_is_available_for(factory, interval, self.params).ok:
                return False
        return True

    # ---- persistence ----

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict.

        ``id`` is intentionally NOT persisted — ids are re-issued on
        hydrate so they stay process-monotonic and don't collide
        across import/export cycles.
        """
        return {
            "kind_id": self.kind_id,
            "kind_version": self.kind_version,
            "display_name": self.display_name,
            "params": dict(self.params),
            "style": {k: {"color": s.color, "width": s.width,
                          "visible": s.visible}
                      for k, s in self.style.items()},
            "intervals": list(self.intervals),
            "scopes": sorted(self.scopes),
            "visible": self.visible,
            "origin": self.origin,
            "pane_group": self.pane_group,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> IndicatorConfig:
        """Hydrate from :meth:`to_dict` output. Marks ``unknown=True``
        when ``kind_id`` is not currently registered."""
        kind_id = str(d.get("kind_id", ""))
        params = dict(d.get("params") or {})
        # Capture the original kind_id BEFORE migration so we can apply
        # output-key remapping (legacy ``rvol_z_*`` configs persisted
        # ``style["z"]`` keys; the unified ``rvol`` indicator emits
        # ``"rvol"``, so we remap the user's customised colour /
        # visibility / width entries to the new key).
        legacy_z_remap = kind_id in _LEGACY_Z_OUTPUT_KIND_IDS
        # Same for the MA family: legacy ``sma`` / ``ema`` configs
        # persisted ``style["sma"]`` / ``style["ema"]`` keys; the
        # unified ``ma`` indicator emits ``"ma"``, so any prior key
        # named after the legacy id must be remapped.
        legacy_ma_key = _LEGACY_MA_OUTPUT_KEYS.get(kind_id)
        # Apply legacy kind_id migrations BEFORE the unknown-flag check
        # so configs persisted under a now-folded id (e.g. "bbands_ema",
        # "atr_sma", "rvol_*", "sma"/"ema") seamlessly hydrate as the
        # unified replacement with the appropriate discriminator param
        # baked in.
        kind_id, params = migrate_kind_id(kind_id, params, include_chart_only=True)
        if kind_id == "avwap":
            params = _migrate_avwap_anchor_params(params)
        unknown = factory_by_kind_id(kind_id) is None
        style = {}
        for k, sd in (d.get("style") or {}).items():
            try:
                # Remap legacy z-score "z" output key → unified "rvol".
                if legacy_z_remap and k == "z":
                    style_key = "rvol"
                elif legacy_ma_key is not None and k == legacy_ma_key:
                    style_key = "ma"
                else:
                    style_key = k
                style[style_key] = LineStyle(
                    color=str(sd.get("color", FALLBACK_GRAY)),
                    width=float(sd.get("width", 1.2)),
                    visible=bool(sd.get("visible", True)),
                )
            except (TypeError, ValueError):
                # Drop malformed style entries; default_style fills in.
                continue
        scopes = d.get("scopes")
        if scopes is None:
            scopes_fs: frozenset[str] = DEFAULT_SCOPES
        else:
            scopes_fs = frozenset(str(s) for s in scopes if s in SCOPES) or DEFAULT_SCOPES
        return cls(
            kind_id=kind_id,
            kind_version=int(d.get("kind_version", 1)),
            display_name=str(d.get("display_name", "")),
            params=params,
            style=style,
            intervals=tuple(str(s) for s in (d.get("intervals") or ())),
            scopes=scopes_fs,
            visible=bool(d.get("visible", True)),
            origin=str(d.get("origin", "builtin")),
            unknown=unknown,
            pane_group=str(
                d.get("pane_group")
                or _factory_pane_group(kind_id)
            ),
        )


# --- Manager -----------------------------------------------------------------

# Subscriber callable: ``cb(event_kind: str, config: IndicatorConfig | None)``.
Subscriber = Callable[[str, IndicatorConfig | None], None]

# Optional scheduler: ``schedule(callback)`` runs callback on the Tk
# thread "soon" (e.g. ``Tk.after_idle``). When None, runs inline.
Scheduler = Callable[[Callable[[], None]], None]


class IndicatorManager:
    """Owns the active indicator config list, the named presets, and
    fires observer callbacks on every mutation.

    Thread model: ALL mutating methods are expected to be called from
    the Tk main thread and are enforced via ``@require_tk_thread``.
    Observer callbacks are dispatched on whatever thread invoked the
    mutation; subscribers that touch Tk should funnel through the
    scheduler.
    """

    def __init__(self, scheduler: Scheduler | None = None) -> None:
        self._configs: list[IndicatorConfig] = []
        self._presets: dict[str, list[IndicatorConfig]] = {}
        self._active_preset: str | None = None
        self._subscribers: list[Subscriber] = []
        self._scheduler = scheduler
        # Coalescing flag: True between the first mutation in a tick
        # and the scheduled redraw firing.
        self._redraw_pending = False

    # ---- subscribers ----

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register a change callback. Returns an unsubscribe handle."""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass
        return _unsubscribe

    def _notify(self, event: str, config: IndicatorConfig | None) -> None:
        # Snapshot so that a callback adding/removing subscribers
        # doesn't corrupt iteration.
        for cb in list(self._subscribers):
            try:
                cb(event, config)
            except Exception:
                # Subscribers must not break the chain. Errors are the
                # subscriber's responsibility (the app's redraw hook
                # logs to status).
                pass

    def _schedule_redraw(self) -> None:
        """Coalesce a redraw event for the end of the current tick.

        First mutation in a quiet period schedules a single
        ``("redraw", None)`` event. Subsequent mutations within the
        same tick are absorbed into the same redraw.
        """
        if self._redraw_pending:
            return
        self._redraw_pending = True

        def _fire() -> None:
            self._redraw_pending = False
            self._notify("redraw", None)

        if self._scheduler is None:
            _fire()
        else:
            self._scheduler(_fire)

    # ---- list accessors ----

    def list(self) -> builtins.list[IndicatorConfig]:
        """Return a SHALLOW copy of the active list (safe to iterate)."""
        return list(self._configs)

    def get(self, config_id: int) -> IndicatorConfig | None:
        for c in self._configs:
            if c.id == config_id:
                return c
        return None

    def applicable(self, scope: str, interval: str) -> builtins.list[IndicatorConfig]:
        """Filter active list to configs that should render in
        ``(scope, interval)``."""
        return [c for c in self._configs if c.applies_to(scope, interval)]

    def __len__(self) -> int:
        return len(self._configs)

    # ---- mutation ----

    @require_tk_thread
    def add(self, config: IndicatorConfig) -> IndicatorConfig:
        self._configs.append(config)
        self._notify("add", config)
        self._schedule_redraw()
        return config

    @require_tk_thread
    def remove(self, config_id: int) -> bool:
        for i, c in enumerate(self._configs):
            if c.id == config_id:
                self._configs.pop(i)
                self._notify("remove", c)
                self._schedule_redraw()
                return True
        return False

    @require_tk_thread
    def update(self, config_id: int, **changes: Any) -> bool:
        c = self.get(config_id)
        if c is None:
            return False
        for k, v in changes.items():
            if hasattr(c, k):
                setattr(c, k, v)
        # If params changed, refresh display_name unless user customized.
        self._notify("update", c)
        self._schedule_redraw()
        return True

    @require_tk_thread
    def clear(self) -> None:
        self._configs.clear()
        self._notify("clear", None)
        self._schedule_redraw()

    @require_tk_thread
    def reorder(self, config_id: int, new_index: int) -> bool:
        """Move the indicator config with ``config_id`` to ``new_index``.

        Index is clamped to ``[0, len(self) - 1]``. Returns ``True`` if
        the config was found (even if the move was a no-op because the
        clamped target equals the current index); ``False`` if no
        config has that id.

        Fires a ``"reorder"`` event with the moved config and then
        schedules a coalesced redraw. Existing subscribers that don't
        recognise ``"reorder"`` will simply skip it and rely on the
        subsequent ``"redraw"`` for repaint; subscribers that DO want
        to react ahead of time (e.g. the indicator dialog re-syncing
        row order) can listen for it.
        """
        for i, c in enumerate(self._configs):
            if c.id == config_id:
                if not self._configs:
                    return True
                target = max(0, min(new_index, len(self._configs) - 1))
                if target != i:
                    self._configs.pop(i)
                    self._configs.insert(target, c)
                self._notify("reorder", c)
                self._schedule_redraw()
                return True
        return False

    # ---- presets ----

    def list_presets(self) -> builtins.list[str]:
        return sorted(self._presets.keys())

    def active_preset(self) -> str | None:
        return self._active_preset

    @require_tk_thread
    def save_preset(self, name: str) -> None:
        """Snapshot the current active list under ``name`` (overwrites)."""
        self._presets[name] = [copy.deepcopy(c) for c in self._configs]
        self._active_preset = name
        self._notify("preset_saved", None)

    @require_tk_thread
    def delete_preset(self, name: str) -> bool:
        if name not in self._presets:
            return False
        del self._presets[name]
        if self._active_preset == name:
            self._active_preset = None
        self._notify("preset_deleted", None)
        return True

    @require_tk_thread
    def set_preset(self, name: str) -> bool:
        """Atomically replace the active list with the named preset."""
        if name not in self._presets:
            return False
        snapshot = self._presets[name]
        # Re-issue ids so observers can distinguish the new instances
        # from any held references to the prior set.
        new_list: list[IndicatorConfig] = []
        for src in snapshot:
            clone = copy.deepcopy(src)
            clone.id = _next_id()
            new_list.append(clone)
        self._configs = new_list
        self._active_preset = name
        self._notify("preset_loaded", None)
        self._schedule_redraw()
        return True

    # ---- persistence ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_configs": [c.to_dict() for c in self._configs],
            "presets": {
                name: [c.to_dict() for c in configs]
                for name, configs in self._presets.items()
            },
            "active_preset": self._active_preset,
        }

    def presets_to_dict(self) -> dict[str, builtins.list[dict]]:
        """Serialize ONLY the named presets (no active-config list).

        Feeds the auto-persist preset store
        (:mod:`indicators.preset_store`). Mirrors the ``presets`` portion
        of :meth:`to_dict` so the on-disk preset envelope round-trips
        through :meth:`install_presets`.
        """
        return {
            name: [c.to_dict() for c in configs]
            for name, configs in self._presets.items()
        }

    @require_tk_thread
    def install_presets(
        self,
        presets: Mapping[str, Iterable[Mapping[str, Any]]],
        active: str | None = None,
    ) -> None:
        """Seed the named-preset table from a persisted snapshot.

        Used at startup to restore presets saved in a prior session via
        :mod:`indicators.preset_store`. Replaces ONLY the preset table and
        the active-preset pointer; the live active-config list is left
        untouched. Deliberately does **not** fire ``preset_saved`` /
        ``preset_loaded`` (so it never re-triggers the auto-persist write)
        and does **not** schedule a redraw — presets are inert until the
        user applies one via :meth:`set_preset`.

        Malformed individual entries are skipped defensively; unknown
        ``kind_id`` payloads hydrate as disabled placeholders (mirroring
        :meth:`load_dict`).
        """
        new_presets: dict[str, list[IndicatorConfig]] = {}
        for name, items in presets.items():
            configs: list[IndicatorConfig] = []
            for item in items:
                try:
                    configs.append(IndicatorConfig.from_dict(item))
                except Exception:  # noqa: BLE001
                    continue
            new_presets[str(name)] = configs
        self._presets = new_presets
        self._active_preset = (
            str(active) if active and str(active) in new_presets else None
        )

    @require_tk_thread
    def load_dict(self, d: Mapping[str, Any]) -> builtins.list[str]:
        """Replace state from a previously-serialized dict.

        Returns a list of human-readable WARN strings (one per unknown
        kind_id encountered). Caller is responsible for surfacing them
        in the status log.

        Atomically rebuilds: if any structural error occurs the prior
        state is preserved.
        """
        warnings: list[str] = []

        def _build_list(items: Iterable[Mapping[str, Any]]) -> list[IndicatorConfig]:
            out: list[IndicatorConfig] = []
            for item in items:
                cfg = IndicatorConfig.from_dict(item)
                if cfg.unknown:
                    warnings.append(
                        f"Unknown indicator kind_id={cfg.kind_id!r} loaded as placeholder"
                    )
                out.append(cfg)
            return out

        try:
            new_active = _build_list(d.get("active_configs") or ())
            new_presets: dict[str, list[IndicatorConfig]] = {}
            for name, items in (d.get("presets") or {}).items():
                new_presets[str(name)] = _build_list(items)
            active_preset = d.get("active_preset")
            new_active_preset = str(active_preset) if active_preset else None
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Failed to parse indicator config: {exc!r}")
            return warnings

        self._configs = new_active
        self._presets = new_presets
        self._active_preset = new_active_preset
        self._notify("loaded", None)
        self._schedule_redraw()
        return warnings
