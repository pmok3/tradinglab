"""Per-indicator settings popup spawned by double-clicking a legend row.

The OverlayLegend (`overlay_legend.py`) shows a horizontal pill per
overlay :class:`~tradinglab.indicators.config.IndicatorConfig` in each
price panel. Double-clicking a pill opens a focused, modeless settings
window that exposes ONLY that indicator's row from the Manage Indicators
dialog — same kind dropdown, same scope checkboxes, same per-param
widgets, same live-debounced commit pipeline, same per-output color
swatches, same per-interval visibility checkboxes.

Design notes
============

* **Singleton per ``config_id``**. ``ChartApp._per_indicator_dialogs``
  maps each open popup's config id to its instance. A second double
  click on the same legend row deiconifies / lifts the existing
  window rather than spawning a duplicate. Different config ids can
  coexist (the user can compare two indicators side by side).

* **Re-use, don't duplicate**. The popup is a thin
  :class:`IndicatorDialog` subclass that flips the
  ``restricted_to_config_id`` filter on and replaces only the chrome
  (no Add / Remove Selected / scrollbar — just the one row +
  Close + a footnote). Every parameter widget, validation rule,
  debounce window, and color-swatch palette is inherited unchanged so
  the popup can never drift from the manager dialog.

* **Auto-close on disappearance**. The base ``_on_manager_event``
  closes the popup if the underlying config is removed, the manager
  is cleared, or a preset is loaded (because the new preset re-issues
  ids — the popup's tracked id is no longer meaningful). Closing
  removes the entry from ``ChartApp._per_indicator_dialogs``.

* **Footnote about coupling**. A subtle muted label at the bottom
  reminds users that exit / entry strategies have their own indicator
  configs — editing here does NOT alter any attached strategy. The
  exits/entries layer references indicators by ``kind_id`` via
  ``scanner.model.FieldRef`` (or carries its own dataclass fields, in
  the Chandelier case) rather than by ``IndicatorConfig.id``, so the
  decoupling is structural; the footnote helps users build the right
  mental model.

* **Geometry is not persisted**. ``IndicatorConfig.id`` is a process
  monotonic int reissued on every preset load, so persisting geometry
  by id would silently apply old positions to unrelated configs. The
  popup uses ``center-on-cursor`` instead.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..indicators._palette import FALLBACK_GRAY
from ..indicators.base import LineStyle
from ..indicators.config import SCOPES, IndicatorConfig
from .indicator_dialog import IndicatorDialog

# Pixel size the popup opens at. Picked to comfortably host the widest
# built-in indicator row (Bollinger Bands: kind dropdown + Primary /
# Compare checkboxes + 4 param widgets including the Moving Average
# combobox + per-interval strip + per-output color swatches), plus the
# footnote line. ``IndicatorDialog`` uses 980x560 for the multi-row
# editor; the popup is narrower because it omits the scrollable
# canvas chrome and Add / Remove buttons.
_POPUP_GEOMETRY: str = "780x340"
_POPUP_MINSIZE: tuple = (640, 280)

# Static footnote text rendered at the bottom of the popup body. See
# the module docstring for the rationale. Kept short so it doesn't
# overflow on the narrowest popup width.
_FOOTNOTE_TEXT: str = (
    "Changes apply immediately. "
    "Edits here affect the chart display only — "
    "exit/entry strategies use their own indicator configs."
)

# Map the originating legend pane ("primary" / "compare" / "drilldown")
# to the :mod:`tradinglab.indicators.config` scope id stored on
# :class:`IndicatorConfig.scopes`. ``primary`` is the main chart
# (``"main"`` scope); ``compare`` and ``drilldown`` keep their names.
# Used by the scope-split path to know which scope a "this chart only"
# split should carve off when the popup was opened from a particular
# legend pane.
_SLOT_TO_SCOPE: dict = {
    "primary": "main",
    "compare": "compare",
    "drilldown": "drilldown",
}

# Display label per scope, used by the radio above the row. Order
# matches :data:`tradinglab.indicators.config.SCOPES`.
_SCOPE_LABEL: dict = {
    "main": "Primary chart",
    "compare": "Compare chart",
    "drilldown": "Drilldown chart",
}


class _PerIndicatorDialog(IndicatorDialog):
    """One-row indicator settings popup.

    Constructed via :func:`open_per_indicator_dialog`; do NOT
    instantiate directly so the ``ChartApp._per_indicator_dialogs``
    singleton bookkeeping stays in sync.
    """

    def __init__(self, app: tk.Tk, config_id: int,
                 slot: str | None = None) -> None:
        # Track the originating slot the popup was opened from. The
        # base dialog does not consume this; scope-split logic reads
        # it to decide which scope "this chart only" should carve off.
        # Set BEFORE ``super().__init__`` so ``_build_layout`` can use
        # it when it constructs the scope-split radio.
        self._origin_slot: str | None = slot
        # Scope-split state. The radio above the row lets the user
        # opt into "apply edits only to this chart" before they make
        # the first edit; the split itself runs on first commit.
        # ``_scope_split_done`` flips True after the split so we don't
        # split twice. ``_scope_radio_var`` holds the "all" / "this"
        # selection. ``_scope_radio_frame`` is the wrapper widget the
        # popup hides when the underlying config is single-scope.
        self._scope_split_done: bool = False
        self._scope_radio_var: tk.StringVar | None = None
        self._scope_radio_frame: tk.Frame | None = None
        self._scope_radio_label: ttk.Label | None = None
        # Snapshot the single config BEFORE ``super().__init__``
        # takes the full manager snapshot — the per-indicator cancel
        # restores only this one config (and any scope-split clone).
        mgr = getattr(app, "_indicator_manager", None)
        orig_cfg = mgr.get(config_id) if mgr else None
        self._config_snapshot = orig_cfg.to_dict() if orig_cfg else None
        self._snapshot_config_id = config_id
        super().__init__(app, restricted_to_config_id=config_id)
        # Re-title with the indicator's display name so a user with
        # several popups open can tell them apart at a glance.
        self._refresh_title()
        # Initial visibility for the scope-split radio depends on the
        # current config's scopes (may already be single-scope, in
        # which case the radio stays hidden).
        self._refresh_scope_radio()

    # ------------------------------------------------------------------
    # Chrome override — replaces the scrollable multi-row editor with
    # a flat single-row layout plus a footnote + Close button.
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        """Lay out a single-row popup: outer padding → scope-split
        radio (hidden when single-scope) → row mount frame →
        footnote → bottom Close button."""
        try:
            self.geometry(_POPUP_GEOMETRY)
            self.minsize(*_POPUP_MINSIZE)
        except tk.TclError:
            pass
        outer = tk.Frame(self, padx=8, pady=8)
        outer.pack(fill="both", expand=True)
        # Scope-split radio. Constructed up-front but ``pack_forget``ed
        # immediately; ``_refresh_scope_radio`` re-packs it when the
        # underlying config has 2+ scopes. Placed ABOVE the row so the
        # user sees the decision before they touch any param widget.
        self._scope_radio_var = tk.StringVar(value="all")
        self._scope_radio_frame = tk.Frame(outer)
        self._scope_radio_label = ttk.Label(
            self._scope_radio_frame,
            text="This indicator is shared across multiple charts.",
        )
        self._scope_radio_label.pack(side="left", anchor="w", padx=(0, 8))
        ttk.Radiobutton(
            self._scope_radio_frame, text="Apply edits to all charts",
            variable=self._scope_radio_var, value="all",
        ).pack(side="left", anchor="w", padx=(0, 8))
        # The "this chart" radio's label is set dynamically by
        # ``_refresh_scope_radio`` so it reads "Apply edits only to
        # Primary chart" / "Compare chart" / etc. based on the origin
        # slot. We stash the widget so the refresh can re-label it.
        self._scope_radio_this_btn = ttk.Radiobutton(
            self._scope_radio_frame, text="Apply only to this chart",
            variable=self._scope_radio_var, value="this",
        )
        self._scope_radio_this_btn.pack(side="left", anchor="w")
        # Initial visibility set by ``_refresh_scope_radio`` after
        # ``__init__`` finishes building the row.
        # Row mount point. ``_build_row`` packs its container into
        # ``self._rows_inner``; we reuse the same attribute name so
        # the inherited row-construction code finds its mount target
        # without further customisation.
        self._rows_inner = tk.Frame(outer)
        self._rows_inner.pack(fill="both", expand=True)
        # Footnote.
        footnote_wrap = tk.Frame(outer)
        footnote_wrap.pack(fill="x", pady=(6, 0))
        self._footnote_label = ttk.Label(
            footnote_wrap, text=_FOOTNOTE_TEXT,
            foreground=FALLBACK_GRAY, wraplength=0,
        )
        self._footnote_label.pack(side="left", anchor="w")
        # Bottom bar — Save and Close + Cancel.
        bar = tk.Frame(outer)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="Cancel",
                   command=self._on_cancel).pack(side="right")
        self._save_close_btn = ttk.Button(
            bar, text="Save and Close", command=self._on_save_close,
            state="disabled",
        )
        self._save_close_btn.pack(side="right", padx=(0, 6))
        # Attributes the base IndicatorDialog populates in its own
        # ``_build_layout`` but the per-indicator popup does NOT
        # expose. Stubbed to None so any inherited code that pokes
        # them defensively (none today) can ``getattr`` without a
        # surprise AttributeError.
        self._add_button = None  # type: ignore[assignment]
        self._budget_label = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Row construction — pin the include_radio / include_drag_handle
    # parameters off so the popup row matches the chrome.
    # ------------------------------------------------------------------

    def _build_row(self, cfg, *, parent=None,
                   include_radio: bool = True,
                   include_drag_handle: bool = True):
        # The per-indicator popup never wants the radiobutton (only
        # one row to "select") or the drag handle (no reorder UI). The
        # base ``_reconcile_from_manager`` calls ``self._build_row(cfg)``
        # with no overrides — we intercept here so the popup gets a
        # clean single-row layout without forking the reconcile path.
        return super()._build_row(
            cfg,
            parent=parent,
            include_radio=False,
            include_drag_handle=False,
        )

    def _on_manager_event(self, event: str, cfg) -> None:
        # Refresh title on every external update so renaming /
        # parameter change is mirrored. The base implementation
        # handles the auto-close on remove / clear / preset_loaded.
        super()._on_manager_event(event, cfg)
        if event == "update" and cfg is not None and \
                getattr(cfg, "id", None) == self._restricted_to_config_id:
            self._refresh_title()
            # External update may have changed the scope set (e.g.
            # the user un-checked Compare in the manager dialog). Re-
            # evaluate radio visibility so a now-single-scope config
            # hides the radio.
            self._refresh_scope_radio()

    # ------------------------------------------------------------------
    # Scope-split radio
    # ------------------------------------------------------------------

    def _refresh_scope_radio(self) -> None:
        """Show/hide the scope-split radio based on the current config.

        The radio is shown when the underlying ``IndicatorConfig``
        applies to 2+ scopes from :data:`SCOPES` (a typical case:
        the same SMA being shown on both the Primary and Compare
        panes). It is hidden after a split has run, or when the
        config is already single-scope.
        """
        frame = self._scope_radio_frame
        if frame is None:
            return
        if self._scope_split_done:
            try:
                frame.pack_forget()
            except tk.TclError:
                pass
            return
        cfg = None
        if self._restricted_to_config_id is not None:
            cfg = self._manager.get(self._restricted_to_config_id)
        active_scopes = set()
        if cfg is not None:
            active_scopes = {s for s in cfg.scopes if s in SCOPES}
        if len(active_scopes) < 2:
            try:
                frame.pack_forget()
            except tk.TclError:
                pass
            # Reset the radio var so a future re-multi-scope config
            # doesn't surprise the user with a stale "this" selection.
            if self._scope_radio_var is not None:
                try:
                    self._scope_radio_var.set("all")
                except tk.TclError:
                    pass
            return
        # Multi-scope: label the "this" radio with the actual chart
        # name based on which legend pane the popup was opened from.
        slot_scope = _SLOT_TO_SCOPE.get(self._origin_slot or "")
        if slot_scope not in active_scopes:
            # The popup was opened from a slot whose scope is no
            # longer on this config (rare race). Fall back to the
            # generic label and let "this" mean "the first remaining
            # scope" — but never lie about which chart.
            slot_scope = sorted(active_scopes)[0]
        chart_label = _SCOPE_LABEL.get(slot_scope, "this chart")
        if self._scope_radio_this_btn is not None:
            try:
                self._scope_radio_this_btn.configure(
                    text=f"Apply only to {chart_label}",
                )
            except tk.TclError:
                pass
        # Pack at the top of ``outer`` — above ``_rows_inner``. Use
        # ``pack(before=...)`` so a re-pack after pack_forget restores
        # the original stacking order.
        try:
            frame.pack(fill="x", pady=(0, 6),
                       before=self._rows_inner)
        except tk.TclError:
            pass

    def _perform_scope_split(self, row) -> bool:
        """Carve a new single-scope clone off the popup's current
        config so subsequent edits on this popup affect only the
        originating chart.

        Returns ``True`` if the split ran (so the caller can update
        ``row.config_id`` before delegating to the base
        ``_commit_now``). ``False`` is a no-op signal: the caller
        should proceed normally.

        Ordering (matters for the base manager-event filter):
          1. Clone the original via ``to_dict()`` round-trip so the
             new :class:`IndicatorConfig` has a fresh ``id``.
          2. Add the clone to the manager. The ``add`` event is
             ignored by the popup's filter (still tracking the
             original id at this point).
          3. Re-point ``self._restricted_to_config_id`` AND the
             registry slot to the clone's id BEFORE updating the
             original — otherwise the next ``update`` event would
             re-match the (now-stale) original id and trigger a
             reconcile that would yank the popup's row out from
             under the user.
          4. Update the original to drop the carved-off scope.
          5. Mutate the row's scope checkbox / preserved-scope state
             so the immediately-following ``super()._commit_now``
             does not overwrite the clone's single scope back to
             the original's full scope set.
        """
        if self._restricted_to_config_id is None:
            return False
        if self._scope_split_done:
            return False
        orig = self._manager.get(self._restricted_to_config_id)
        if orig is None:
            return False
        active_scopes = {s for s in orig.scopes if s in SCOPES}
        if len(active_scopes) < 2:
            return False
        slot_scope = _SLOT_TO_SCOPE.get(self._origin_slot or "")
        if slot_scope is None or slot_scope not in active_scopes:
            slot_scope = sorted(active_scopes)[0]
        clone = IndicatorConfig.from_dict(orig.to_dict())
        clone.scopes = frozenset({slot_scope})
        clone.unknown = bool(orig.unknown)
        # Reconciling guard: every manager mutation between here and
        # the end of this method must not trigger our own reconcile.
        # The base ``_commit_now`` does this on its own segment of
        # the work; we extend the same guard across the split.
        self._reconciling = True
        try:
            added = self._manager.add(clone)
            # Re-point the popup to the clone BEFORE updating the
            # original so the original's scope-change event no
            # longer matches our restricted filter.
            registry = getattr(self._app, "_per_indicator_dialogs", None)
            if isinstance(registry, dict):
                if registry.get(orig.id) is self:
                    try:
                        del registry[orig.id]
                    except KeyError:
                        pass
                registry[added.id] = self
            dlg_mgr = getattr(self._app, "_dialog_mgr", None)
            if dlg_mgr is not None:
                try:
                    dlg_mgr.rekey(_dialog_key(orig.id), _dialog_key(added.id), self)
                except Exception:  # noqa: BLE001
                    pass
            self._restricted_to_config_id = added.id
            row.config_id = added.id
            # Carve the scope off the original. ``_build_scopes`` on
            # the row sets ``preserved_active_scopes`` to the row's
            # current scope set; the original keeps whatever the
            # other charts are using.
            remaining = orig.scopes - {slot_scope}
            self._manager.update(orig.id, scopes=frozenset(remaining))
            # Mutate the row's scope-checkbox vars + preserved-extra
            # set so the base ``_commit_now``'s subsequent call to
            # ``_build_scopes`` produces ``{slot_scope}`` (matches
            # the clone) rather than the original's full scope set
            # (which would silently re-broaden the clone back to
            # multi-scope, defeating the split). ``row.suppress``
            # prevents the var traces from triggering a recursive
            # commit while we're already inside one.
            row.suppress = True
            try:
                if row.primary_var is not None:
                    row.primary_var.set(slot_scope == "main")
                if row.compare_var is not None:
                    row.compare_var.set(slot_scope == "compare")
                # Drop any preserved extra scopes (e.g. drilldown
                # from the original) — the clone is intentionally
                # single-scope.
                row.preserved_extra_scopes = frozenset()
                row.preserved_active_scopes = frozenset({slot_scope})
            finally:
                row.suppress = False
        finally:
            self._reconciling = False
        self._scope_split_done = True
        return True

    def _commit_now(self, row) -> None:
        """Validate-then-split-then-commit.

        Wraps the base :meth:`IndicatorDialog._commit_now` so a
        per-indicator popup with the scope-split radio set to
        "this chart" carves a single-scope clone off the underlying
        config BEFORE the actual ``manager.update`` lands. The clone
        is created with the original's current params; the base
        commit then applies the user's pending edits to the clone.

        A pre-validation pass (instantiate the factory with the
        candidate params) ensures we don't split on bad input — if
        the user typed something invalid, the base ``_commit_now``
        would revert anyway, and we'd be left with a useless clone.

        The split is suppressed when the proposed scopes differ
        from the original's scopes: that means the user is making
        an explicit scope change in the row itself (e.g. unchecking
        Compare), which should apply to the shared config without
        cloning.
        """
        # Defer to base behaviour when split is not applicable.
        if row.suppress or self._reconciling or row.is_unknown:
            super()._commit_now(row)
            return
        if (self._scope_split_done
                or self._scope_radio_var is None
                or self._scope_radio_var.get() != "this"):
            super()._commit_now(row)
            return
        orig = (self._manager.get(self._restricted_to_config_id)
                if self._restricted_to_config_id is not None else None)
        if orig is None:
            super()._commit_now(row)
            return
        active_scopes = {s for s in orig.scopes if s in SCOPES}
        if len(active_scopes) < 2:
            super()._commit_now(row)
            return
        # Pre-validate so a malformed param doesn't cause a wasted
        # split. The base ``_commit_now`` performs the same check
        # again; running it twice is cheap (single factory call).
        try:
            from ..indicators.base import factory_by_kind_id
        except Exception:  # noqa: BLE001
            super()._commit_now(row)
            return
        try:
            kind_display = (row.kind_var.get() or "").strip()
            kind_id = self._kinds_by_display.get(kind_display)
            if not kind_id:
                super()._commit_now(row)
                return
            pair = factory_by_kind_id(kind_id)
            if pair is None:
                super()._commit_now(row)
                return
            _name, cls = pair
            params = self._collect_param_values(row)
            cls(**params)  # validation
        except Exception:  # noqa: BLE001
            # Validation failed — let the base path handle the
            # revert. No split.
            super()._commit_now(row)
            return
        # Skip the split if the user's edit IS a scope change
        # (proposed scopes != original's). The shared-config
        # update is the right thing in that case.
        try:
            proposed_scopes, _vis = self._build_scopes(row)
        except Exception:  # noqa: BLE001
            super()._commit_now(row)
            return
        if frozenset(proposed_scopes) != frozenset(orig.scopes):
            super()._commit_now(row)
            return
        # All preconditions met — perform the split, then apply the
        # user's pending edits to the clone via the base commit.
        self._perform_scope_split(row)
        super()._commit_now(row)
        # After the split the underlying config is single-scope; the
        # radio's reason for existence is gone. Hide it.
        self._refresh_scope_radio()

    # ------------------------------------------------------------------
    # Lifecycle — cancel reverts the single config; save persists all.
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        """Revert this indicator to its pre-popup state, then close.

        If a scope-split happened, the clone is removed and the
        original config's scopes are restored. Otherwise the
        original config's attributes are restored from the snapshot.
        """
        cid = self._restricted_to_config_id
        if self._dirty and self._config_snapshot is not None:
            self._reconciling = True
            try:
                if self._scope_split_done:
                    # Remove the clone created by the split.
                    if cid is not None:
                        self._manager.remove(cid)
                    # Restore original config's scopes.
                    snap = self._config_snapshot
                    orig_scopes = frozenset(
                        s for s in (snap.get("scopes") or ["main"])
                        if s in SCOPES
                    ) or frozenset({"main"})
                    self._manager.update(
                        self._snapshot_config_id,
                        scopes=orig_scopes,
                    )
                else:
                    # Restore all attributes of the single config.
                    snap = self._config_snapshot
                    style = {}
                    for k, sd in (snap.get("style") or {}).items():
                        try:
                            style[k] = LineStyle(
                                color=str(sd.get("color", FALLBACK_GRAY)),
                                width=float(sd.get("width", 1.2)),
                                visible=bool(sd.get("visible", True)),
                            )
                        except (TypeError, ValueError):
                            continue
                    self._manager.update(
                        self._snapshot_config_id,
                        kind_id=snap.get("kind_id", ""),
                        kind_version=int(snap.get("kind_version", 1)),
                        display_name=snap.get("display_name", ""),
                        params=dict(snap.get("params") or {}),
                        style=style,
                        intervals=tuple(
                            str(s) for s in (snap.get("intervals") or ())
                        ),
                        scopes=frozenset(
                            s for s in (snap.get("scopes") or ["main"])
                            if s in SCOPES
                        ) or frozenset({"main"}),
                        visible=snap.get("visible", True),
                        pane_group=snap.get("pane_group", ""),
                    )
            finally:
                self._reconciling = False
        self._teardown_popup(cid)

    def _on_save_close(self) -> None:
        """Accept the current live state and close."""
        cid = self._restricted_to_config_id
        self._config_snapshot = None  # discard the revert point
        self._teardown_popup(cid)

    def _on_close(self) -> None:  # noqa: D401
        """Alias — WM_DELETE_WINDOW and Escape route here."""
        self._on_cancel()

    def _teardown_popup(self, cid: int | None) -> None:
        """Mechanical teardown + registry cleanup."""
        try:
            self._teardown()
        finally:
            registry = getattr(self._app, "_per_indicator_dialogs", None)
            if isinstance(registry, dict) and cid is not None:
                if registry.get(cid) is self:
                    try:
                        del registry[cid]
                    except KeyError:
                        pass
            dlg_mgr = getattr(self._app, "_dialog_mgr", None)
            if dlg_mgr is not None and cid is not None:
                try:
                    dlg_mgr.forget(_dialog_key(cid), self)
                except Exception:  # noqa: BLE001
                    pass

    def _refresh_title(self) -> None:
        """Set the Toplevel title to ``"Edit <display_name>"`` with
        optional dirty indicator."""
        cfg = self._manager.get(self._restricted_to_config_id or -1) \
            if self._restricted_to_config_id is not None else None
        label = "Indicator"
        if cfg is not None:
            label = (cfg.display_name or cfg.kind_id or "Indicator").strip()
        title = f"Edit {label}"
        self._base_title = title
        if getattr(self, "_dirty", False):
            title += " \u2022"
        try:
            self.title(title)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Window placement
    # ------------------------------------------------------------------

    def center_on_cursor(self) -> None:
        """Position the popup so its top-left sits near the mouse
        cursor without going off-screen.

        Geometry is NOT persisted (``IndicatorConfig.id`` is process
        monotonic and re-issued on hydrate, so a persisted location
        could silently bind to a different indicator on the next
        preset load)."""
        try:
            x = self.winfo_pointerx() - 60
            y = self.winfo_pointery() - 20
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            req_w = int(self.winfo_reqwidth() or 780)
            req_h = int(self.winfo_reqheight() or 320)
            x = max(0, min(x, screen_w - req_w - 20))
            y = max(0, min(y, screen_h - req_h - 40))
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass


def _dialog_key(config_id: int) -> str:
    return f"per_indicator:{int(config_id)}"


def open_per_indicator_dialog(
    app: tk.Tk, config_id: int,
    slot: str | None = None,
) -> _PerIndicatorDialog | None:
    """Open or refocus the singleton popup for ``config_id``.

    Returns the popup instance, or ``None`` if the config is not
    present on the manager (defensive guard for double-clicks that
    race a concurrent remove). ``slot`` records which legend pane
    the user clicked from (``"primary"`` / ``"compare"``); it is
    stashed on the popup as ``_origin_slot`` for scope-split logic
    introduced in later increments.
    """
    registry = getattr(app, "_per_indicator_dialogs", None)
    if not isinstance(registry, dict):
        registry = {}
        try:
            app._per_indicator_dialogs = registry  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return None
    dialog_key = _dialog_key(config_id)
    dlg_mgr = getattr(app, "_dialog_mgr", None)
    if dlg_mgr is not None:
        existing = dlg_mgr.get(dialog_key)
        if existing is not None:
            try:
                existing._origin_slot = slot
                try:
                    existing._refresh_scope_radio()
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
        else:
            manager = getattr(app, "_indicator_manager", None)
            if manager is None:
                return None
            cfg = manager.get(config_id)
            if cfg is None:
                return None

        def _factory() -> _PerIndicatorDialog:
            dlg = _PerIndicatorDialog(app, config_id, slot=slot)
            registry[config_id] = dlg
            return dlg

        dlg = dlg_mgr.open_or_focus(dialog_key, _factory)
        registry[config_id] = dlg
        try:
            dlg._origin_slot = slot
        except Exception:  # noqa: BLE001
            pass
        if existing is None:
            try:
                dlg.center_on_cursor()
            except Exception:  # noqa: BLE001
                pass
        return dlg
    existing = registry.get(config_id)
    if existing is not None:
        try:
            if existing.winfo_exists():
                try:
                    existing._origin_slot = slot
                    # Re-evaluate radio label / visibility now that
                    # the slot has changed (the user may have re-
                    # double-clicked from the OTHER chart's legend).
                    try:
                        existing._refresh_scope_radio()
                    except Exception:  # noqa: BLE001
                        pass
                    existing.deiconify()
                    existing.lift()
                    existing.focus_set()
                except tk.TclError:
                    pass
                return existing
        except tk.TclError:
            pass
        # Stale ref (window destroyed without going through
        # ``_on_close`` — e.g. parent Toplevel destroyed via
        # ``destroy()``); fall through and recreate.
        try:
            del registry[config_id]
        except KeyError:
            pass
    # Defensive: make sure the config still exists on the manager
    # before opening. A user could legitimately rapid-click two
    # legends; the second click might race a remove on the first.
    manager = getattr(app, "_indicator_manager", None)
    if manager is None:
        return None
    cfg = manager.get(config_id)
    if cfg is None:
        return None
    dlg = _PerIndicatorDialog(app, config_id, slot=slot)
    registry[config_id] = dlg
    try:
        dlg.center_on_cursor()
    except Exception:  # noqa: BLE001
        pass
    return dlg


__all__ = ("open_per_indicator_dialog", "_PerIndicatorDialog")
