"""Custom Indicator Builder dialog.

Modal Toplevel reachable from **Indicators → Custom Indicator Builder…**
(menu entry sits directly under *Manage Indicators…*). Lets the user:

* List, edit, and delete the ``.py`` files in the custom-indicators
  directory (``%LOCALAPPDATA%\\TradingLab\\indicators``) that were
  authored via this dialog.
* Author a new indicator either as a **building-blocks expression**
  (parsed + AST-whitelisted; safe by construction — see
  :mod:`tradinglab.indicators.expression`) or as a **Python module**
  (full ``exec``; gated behind a per-save "this is arbitrary Python"
  confirmation prompt).
* Validate + preview the indicator on the current chart's candles
  (last 200 bars, embedded matplotlib canvas).
* Save → write atomically to disk → live-register via the existing
  loader so the indicator immediately appears in the chart Add
  Indicator menu and in the entry/exit strategy trigger dropdowns.

The dialog is intentionally lighter than :class:`IndicatorDialog`
(``indicator_dialog.py``) — it owns a single "current edit" form
rather than reconciling against a live manager. Saves are explicit.

See also:
* :mod:`tradinglab.indicators.expression` — parser / codegen.
* :mod:`tradinglab.indicators.loader` — discovery / hot-reload /
  unregister.
"""
from __future__ import annotations

import os
import tempfile
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from ..indicators import loader as ind_loader
from ..indicators._palette import FALLBACK_GRAY
from ..indicators.base import INDICATORS
from ..indicators.expression import (
    ExpressionError,
    conditions_to_python,
    evaluate,
    expression_to_python,
    parse_expression,
    python_mode_wrapper,
    safe_indicator_filename,
    warmup_for_conditions,
)
from ..scanner.model import Group
from ._modal_base import BaseModalDialog, protect_combobox_wheel

__all__ = [
    "CustomIndicatorDialog",
    "open_custom_indicator_dialog",
]


_CONDITIONS_MODE = "Conditions"
_EXPRESSION_MODE = "Expression"
_PYTHON_MODE = "Python"
_MODES = (_CONDITIONS_MODE, _EXPRESSION_MODE, _PYTHON_MODE)

# Back-compat alias so older callers / tests that imported the old
# constant continue to resolve. ``_BUILDING_BLOCKS`` is the on-disk
# header value for the (now-renamed) Expression mode; the dialog label
# changed but the file format key did not, so saved indicators keep
# loading without migration.
_BUILDING_BLOCKS = _EXPRESSION_MODE

_PYTHON_STARTER = '''\
"""Custom indicator (Python mode). Edit me!

The file must define a class that implements:
    name: str
    overlay: bool
    compute_arr(self, bars) -> dict[str, numpy.ndarray]
    warmup_bars: int   # property OR method OR plain attribute

and end with a `register_indicator(name, factory)` call.
"""
import numpy as np
from tradinglab.indicators.base import register_indicator
from tradinglab.core.bars import Bars
from tradinglab.indicators.ma_kernels import ema, sma


class _Indicator:
    name = "__NAME__"
    kind_id = "__NAME__"
    kind_version = 1
    overlay = True
    pane_group = ""

    def compute_arr(self, bars):
        # Example: a 9 EMA minus 20 SMA momentum gauge.
        return {"value": ema(bars.close, 9) - sma(bars.close, 20)}

    def compute(self, candles):
        return self.compute_arr(Bars.from_candles(candles))

    @property
    def warmup_bars(self):
        return 20


register_indicator("__NAME__", lambda: _Indicator())
'''


_BUILTIN_CHEATSHEET = (
    "Series:  close  open  high  low  volume  hl2  hlc3  ohlc4\n"
    "MAs:     ema(s, n)  sma(s, n)  wma(s, n)  rma(s, n)\n"
    "Other:   rsi(s, n)  atr(n)  vwap()  highest(s, n)  lowest(s, n)\n"
    "Bands:   bollinger(s, n, k)  bollinger_upper/lower(s, n, k)\n"
    "MACD:    macd(s, fast, slow, signal)  macd_signal/hist(...)\n"
    "Math:    abs sqrt log exp max min   |   where(cond, then, else)\n"
    "Ops:     + - * / ** %    < <= > >= == !=    and or not"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_header_metadata(path: Path) -> dict[str, str]:
    """Parse the ``# tradinglab-custom-indicator`` header into a dict.

    Returns an empty dict if the file is not a builder-managed file
    (no header). Keys observed: ``mode``, ``expression``,
    ``description``, ``created``, ``updated``.
    """
    meta: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            for _i, line in enumerate(fh):
                if _i > 10:
                    break
                line = line.rstrip("\n")
                if not line.startswith("#"):
                    if meta:
                        break
                    continue
                if line.strip() == "# tradinglab-custom-indicator":
                    meta["_marker"] = "yes"
                    continue
                content = line.lstrip("#").strip()
                if ":" in content:
                    key, _, val = content.partition(":")
                    meta[key.strip().lower()] = val.strip()
    except OSError:
        pass
    return meta


def open_custom_indicator_dialog(app: tk.Tk) -> CustomIndicatorDialog:
    """Open or focus a singleton :class:`CustomIndicatorDialog` on ``app``."""
    existing = getattr(app, "_custom_indicator_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_set()
                return existing
        except tk.TclError:
            pass
    dlg = CustomIndicatorDialog(app)
    try:
        app._custom_indicator_dialog = dlg  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return dlg


class CustomIndicatorDialog(BaseModalDialog):
    """Two-pane modal: saved-indicators list on the left, editor on the right."""

    def __init__(self, app: tk.Misc, *, directory: Path | None = None) -> None:
        super().__init__(
            app,
            title="Custom Indicator Builder",
            geometry_key="dlg.custom_indicator",
            default_geometry="980x720",
            apply_dark_theme=True,
        )
        self._app = app
        self._directory: Path = directory or ind_loader.default_user_dir()
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        # Form state vars.
        self._name_var = tk.StringVar(value="")
        self._desc_var = tk.StringVar(value="")
        self._mode_var = tk.StringVar(value=_CONDITIONS_MODE)
        self._overlay_var = tk.BooleanVar(value=False)
        # "Expose to scanner" — when True, the generated source embeds
        # ``scannable_outputs = (("value", "numeric"),)`` so the indicator
        # opts into the scanner / entries / exits dropdowns. Defaults to
        # OFF so the fail-closed policy from
        # :mod:`tradinglab.scanner.fields` still holds.
        self._scannable_var = tk.BooleanVar(value=False)
        self._status_var = tk.StringVar(value="")
        # Currently-loaded file path (None for "new").
        self._current_path: Path | None = None
        # Currently-loaded mode read from disk; survives mode-var
        # changes so we can detect "the user switched modes after
        # loading" if needed.
        self._loaded_mode: str | None = None

        # Per-mode body state, preserved across mode switches so the
        # user doesn't lose work when toggling the mode picker.
        self._group_root: Group = Group(combinator="and", children=[])
        self._expression_text_cached: str = ""
        self._python_text_cached: str = ""

        self._build_layout()
        self._refresh_saved_list()
        self._set_status("")
        self._finalize_modal(grab=False)
        # Re-apply wheel guard AFTER finalize so the combobox is
        # discoverable (it lives inside scroll_frame).
        try:
            protect_combobox_wheel(self, scroll_target=None)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True, padx=10, pady=10)

        # Left: saved list.
        left = ttk.LabelFrame(body, text="Saved indicators", padding=6)
        left.pack(side="left", fill="y", padx=(0, 8))
        self._listbox = tk.Listbox(left, width=24, height=18, exportselection=False)
        self._listbox.pack(side="top", fill="both", expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select_saved)
        btn_row = ttk.Frame(left)
        btn_row.pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Button(btn_row, text="New", command=self._on_new).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Delete", command=self._on_delete).pack(
            side="left", padx=2,
        )

        # Right: editor.
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        meta = ttk.Frame(right)
        meta.pack(side="top", fill="x")
        ttk.Label(meta, text="Name:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttk.Entry(meta, textvariable=self._name_var, width=28).grid(
            row=0, column=1, sticky="w",
        )
        ttk.Label(meta, text="Mode:").grid(row=0, column=2, sticky="e", padx=(12, 4))
        self._mode_combo = ttk.Combobox(
            meta, textvariable=self._mode_var, values=list(_MODES),
            state="readonly", width=18,
        )
        self._mode_combo.grid(row=0, column=3, sticky="w")
        self._mode_combo.bind("<<ComboboxSelected>>", self._on_mode_changed)
        ttk.Checkbutton(
            meta, text="Overlay on price pane", variable=self._overlay_var,
        ).grid(row=0, column=4, sticky="w", padx=(12, 0))
        ttk.Checkbutton(
            meta,
            text="Expose to scanner",
            variable=self._scannable_var,
        ).grid(row=0, column=5, sticky="w", padx=(12, 0))

        ttk.Label(meta, text="Description:").grid(
            row=1, column=0, sticky="w", padx=(0, 4), pady=(4, 0),
        )
        ttk.Entry(meta, textvariable=self._desc_var, width=64).grid(
            row=1, column=1, columnspan=5, sticky="we", pady=(4, 0),
        )
        meta.columnconfigure(1, weight=1)

        # Composition area — swapped on mode change.
        self._compose_frame = ttk.LabelFrame(right, text="Composition", padding=6)
        self._compose_frame.pack(side="top", fill="both", expand=True, pady=(8, 4))

        self._cheatsheet_lbl: ttk.Label | None = None
        self._expr_text: tk.Text | None = None
        self._python_text: tk.Text | None = None
        self._block_editor: Any = None  # BlockEditor instance, lazy

        self._render_compose_for_mode()

        # Validate / Preview / Save / Cancel buttons.
        actions = ttk.Frame(right)
        actions.pack(side="top", fill="x", pady=(2, 2))
        ttk.Button(actions, text="Validate", command=self._on_validate).pack(
            side="left", padx=2,
        )
        ttk.Button(actions, text="Preview", command=self._on_preview).pack(
            side="left", padx=2,
        )
        ttk.Button(actions, text="Save", command=self._on_save).pack(
            side="right", padx=2,
        )
        ttk.Button(actions, text="Close", command=self._on_cancel).pack(
            side="right", padx=2,
        )

        # Preview canvas. Starts collapsed (non-expanding) so the
        # Composition area owns the full vertical budget — a
        # parameter-heavy Conditions/BlockEditor body must never be
        # squeezed by an empty preview pane. Expanded on first render
        # (see :meth:`_render_preview`) and re-collapsed when the body
        # is reset for a new indicator.
        self._preview_frame = ttk.LabelFrame(right, text="Preview", padding=4)
        self._preview_frame.pack(side="top", fill="x", expand=False, pady=(4, 2))
        self._preview_expanded = False
        self._preview_canvas: Any = None  # FigureCanvasTkAgg, lazy
        ttk.Label(
            self._preview_frame,
            text="(Click Preview to render the indicator on the current chart's candles.)",
            foreground=FALLBACK_GRAY,
        ).pack(side="top", anchor="w")

        # Status bar.
        status = ttk.Frame(self)
        status.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        self._status_lbl = ttk.Label(
            status, textvariable=self._status_var, foreground="#444444",
        )
        self._status_lbl.pack(side="left", fill="x", expand=True)

    def _capture_body_state(self) -> None:
        """Snapshot whichever body widget is currently mounted into the cache vars.

        Called before tearing down a body during a mode switch so the user's
        composition isn't lost. The Conditions body keeps its tree in
        ``self._group_root`` already via the BlockEditor's live mutation,
        but we still pull ``get_root()`` to capture any pending edits.
        """
        if self._expr_text is not None:
            try:
                self._expression_text_cached = self._expr_text.get("1.0", "end").rstrip("\n")
            except tk.TclError:
                pass
        if self._python_text is not None:
            try:
                self._python_text_cached = self._python_text.get("1.0", "end").rstrip("\n")
            except tk.TclError:
                pass
        if self._block_editor is not None:
            try:
                self._group_root = self._block_editor.get_root()
            except Exception:  # noqa: BLE001
                pass

    def _render_compose_for_mode(self) -> None:
        for child in self._compose_frame.winfo_children():
            child.destroy()
        self._expr_text = None
        self._python_text = None
        self._cheatsheet_lbl = None
        self._block_editor = None

        mode = self._mode_var.get()
        if mode == _CONDITIONS_MODE:
            self._render_conditions_body()
        elif mode == _EXPRESSION_MODE:
            self._render_expression_body()
        else:
            self._render_python_body()

        # Re-apply combobox wheel guard AFTER widgets are mounted so the
        # Mode combobox itself plus any new comboboxes inside the
        # embedded BlockEditor are protected (CLAUDE.md §7.11).
        try:
            protect_combobox_wheel(self, scroll_target=None)
        except tk.TclError:
            pass

    def _render_conditions_body(self) -> None:
        from .scanner_block_editor import BlockEditor

        ttk.Label(
            self._compose_frame,
            text=(
                "Build a condition tree (same editor used by entries/exits). "
                "The indicator emits 1.0 when the group is TRUE at a bar, "
                "0.0 when FALSE, NaN during warmup."
            ),
            foreground="#666666", justify="left",
        ).pack(side="top", anchor="w", pady=(0, 4))
        ttk.Label(self._compose_frame, text="Condition tree:").pack(
            side="top", anchor="w",
        )
        self._block_editor = BlockEditor(
            self._compose_frame,
            root=self._group_root,
            on_change=self._on_block_editor_changed,
            default_interval="1d",
        )
        self._block_editor.pack(side="top", fill="both", expand=True)

    def _on_block_editor_changed(self) -> None:
        # Re-apply wheel guard after every edit because the BlockEditor
        # tears down + rebuilds child widgets on Add/Delete/combinator
        # changes; freshly-built widgets start with no bindings.
        try:
            protect_combobox_wheel(self, scroll_target=None)
        except tk.TclError:
            pass
        if self._block_editor is not None:
            try:
                self._group_root = self._block_editor.get_root()
            except Exception:  # noqa: BLE001
                pass

    def _render_expression_body(self) -> None:
        self._cheatsheet_lbl = ttk.Label(
            self._compose_frame,
            text=_BUILTIN_CHEATSHEET,
            font=("Consolas", 9), foreground="#666666",
            justify="left",
        )
        self._cheatsheet_lbl.pack(side="top", anchor="w", pady=(0, 6))
        ttk.Label(self._compose_frame, text="Expression:").pack(
            side="top", anchor="w",
        )
        self._expr_text = tk.Text(
            self._compose_frame, height=6, wrap="word", font=("Consolas", 10),
        )
        self._expr_text.pack(side="top", fill="both", expand=True)
        if self._expression_text_cached:
            self._expr_text.insert("1.0", self._expression_text_cached)

    def _render_python_body(self) -> None:
        ttk.Label(
            self._compose_frame,
            text=(
                "⚠ Python mode executes arbitrary code every time the "
                "indicator is computed.\n"
                "    Only save indicators you trust."
            ),
            foreground="#a02020", justify="left",
        ).pack(side="top", anchor="w", pady=(0, 6))
        ttk.Label(self._compose_frame, text="Python source:").pack(
            side="top", anchor="w",
        )
        self._python_text = tk.Text(
            self._compose_frame, height=22, wrap="none",
            font=("Consolas", 10),
        )
        self._python_text.pack(side="top", fill="both", expand=True)
        if self._python_text_cached:
            self._python_text.insert("1.0", self._python_text_cached)
        else:
            name = self._name_var.get().strip() or "my_indicator"
            self._python_text.insert(
                "1.0", _PYTHON_STARTER.replace("__NAME__", name)
            )

    # ------------------------------------------------------------------
    # Saved-indicator list
    # ------------------------------------------------------------------
    def _builder_files(self) -> list[Path]:
        results: list[Path] = []
        try:
            files = sorted(
                p for p in self._directory.iterdir()
                if p.is_file() and p.suffix == ".py"
            )
        except OSError:
            return results
        for p in files:
            meta = _read_header_metadata(p)
            if meta.get("_marker") == "yes":
                results.append(p)
        return results

    def _refresh_saved_list(self) -> None:
        self._listbox.delete(0, "end")
        for p in self._builder_files():
            self._listbox.insert("end", p.stem)

    def _on_select_saved(self, _event: object = None) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        files = self._builder_files()
        if idx >= len(files):
            return
        self._load_from_file(files[idx])

    def _load_from_file(self, path: Path) -> None:
        meta = _read_header_metadata(path)
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._set_status(f"Cannot read {path.name}: {exc}", level="error")
            return
        mode = (meta.get("mode") or "").strip()
        self._current_path = path
        self._loaded_mode = mode
        self._name_var.set(path.stem)
        self._desc_var.set(meta.get("description", ""))
        # Overlay flag round-trips through the header for conditions mode.
        overlay_str = meta.get("overlay", "").strip().lower()
        if overlay_str in ("true", "false"):
            self._overlay_var.set(overlay_str == "true")
        scannable_str = meta.get("scannable", "").strip().lower()
        if scannable_str in ("true", "false"):
            self._scannable_var.set(scannable_str == "true")
        else:
            self._scannable_var.set(False)
        if mode == "conditions":
            self._mode_var.set(_CONDITIONS_MODE)
            group_json = meta.get("conditions_json", "")
            loaded_group: Group | None = None
            if group_json:
                try:
                    import json as _json
                    loaded_group = Group.from_dict(_json.loads(group_json))
                except Exception as exc:  # noqa: BLE001
                    self._set_status(
                        f"Loaded {path.name} but conditions_json is corrupt: {exc}",
                        level="error",
                    )
            if loaded_group is None:
                loaded_group = Group(combinator="and", children=[])
            self._group_root = loaded_group
            self._render_compose_for_mode()
        elif mode == "building_blocks":
            self._mode_var.set(_EXPRESSION_MODE)
            expr = meta.get("expression", "")
            self._expression_text_cached = expr
            self._render_compose_for_mode()
            if self._expr_text is not None:
                self._expr_text.delete("1.0", "end")
                self._expr_text.insert("1.0", expr)
        else:
            self._mode_var.set(_PYTHON_MODE)
            # Strip the header (lines that start with '#' at top) from
            # display so the user edits the body only.
            body_lines = []
            past_header = False
            for ln in source.splitlines():
                if not past_header and ln.startswith("#"):
                    continue
                past_header = True
                body_lines.append(ln)
            body = "\n".join(body_lines).lstrip("\n")
            self._python_text_cached = body
            self._render_compose_for_mode()
            if self._python_text is not None:
                self._python_text.delete("1.0", "end")
                self._python_text.insert("1.0", body)
        self._set_status(f"Loaded {path.name}", level="info")

    def _on_new(self) -> None:
        self._current_path = None
        self._loaded_mode = None
        self._name_var.set("")
        self._desc_var.set("")
        self._mode_var.set(_CONDITIONS_MODE)
        self._overlay_var.set(False)
        self._scannable_var.set(False)
        # Reset cached body states so the next mode-switch doesn't restore
        # stale composition from a prior load.
        self._group_root = Group(combinator="and", children=[])
        self._expression_text_cached = ""
        self._python_text_cached = ""
        self._render_compose_for_mode()
        self._reset_preview()
        self._listbox.selection_clear(0, "end")
        self._set_status("Editing new indicator", level="info")

    def _on_delete(self) -> None:
        sel = self._listbox.curselection()
        if not sel:
            self._set_status("Select an indicator to delete", level="error")
            return
        idx = sel[0]
        files = self._builder_files()
        if idx >= len(files):
            return
        path = files[idx]
        name = path.stem
        if not messagebox.askyesno(
            "Delete custom indicator",
            f"Delete {name!r}? This removes {path.name} from disk and "
            "unregisters the indicator in this session.",
            parent=self,
        ):
            return
        try:
            path.unlink()
        except OSError as exc:
            self._set_status(f"Delete failed: {exc}", level="error")
            return
        ind_loader.unregister_indicator(name)
        if self._current_path == path:
            self._on_new()
        self._refresh_saved_list()
        self._set_status(f"Deleted {name}", level="info")

    # ------------------------------------------------------------------
    # Mode / validate / preview
    # ------------------------------------------------------------------
    def _on_mode_changed(self, _event: object = None) -> None:
        self._capture_body_state()
        self._render_compose_for_mode()
        self._set_status("Mode switched", level="info")

    def _current_expression(self) -> str:
        if self._expr_text is None:
            return self._expression_text_cached.strip()
        return self._expr_text.get("1.0", "end").strip()

    def _current_python_body(self) -> str:
        if self._python_text is None:
            return self._python_text_cached
        return self._python_text.get("1.0", "end")

    def _current_group(self) -> Group:
        if self._block_editor is not None:
            try:
                return self._block_editor.get_root()
            except Exception:  # noqa: BLE001
                pass
        return self._group_root

    def _validate(self) -> tuple[bool, str]:
        name = self._name_var.get().strip()
        try:
            safe_indicator_filename(name)
        except ExpressionError as exc:
            return False, str(exc)
        mode = self._mode_var.get()
        if mode == _CONDITIONS_MODE:
            grp = self._current_group()
            if not grp.children:
                return False, "Condition tree is empty — add at least one condition"
            try:
                warmup = warmup_for_conditions(grp.to_dict())
            except ExpressionError as exc:
                return False, str(exc)
            return True, f"Condition tree OK (warmup: {warmup} bars)"
        if mode == _EXPRESSION_MODE:
            try:
                expr = self._current_expression()
                if not expr:
                    return False, "Expression is empty"
                parse_expression(expr)
            except ExpressionError as exc:
                return False, str(exc)
            return True, "Expression parses OK"
        body = self._current_python_body()
        if "register_indicator" not in body:
            return False, "Python source must call register_indicator(name, factory)"
        try:
            compile(body, "<custom>", "exec")
        except SyntaxError as exc:
            return False, f"SyntaxError: {exc.msg} (line {exc.lineno})"
        return True, "Python source compiles OK"

    def _on_validate(self) -> None:
        ok, msg = self._validate()
        self._set_status(msg, level="ok" if ok else "error")

    def _on_preview(self) -> None:
        ok, msg = self._validate()
        if not ok:
            self._set_status(msg, level="error")
            return
        # Resolve candles from the active chart.
        candles = list(getattr(self._app, "_primary", None) or [])
        if not candles:
            self._set_status(
                "No candles available on the active chart to preview against.",
                level="error",
            )
            return
        try:
            from ..core.bars import Bars
            bars = Bars.from_candles(candles[-200:])
            mode = self._mode_var.get()
            if mode == _CONDITIONS_MODE:
                out = self._compute_conditions(bars)
            elif mode == _EXPRESSION_MODE:
                expr = parse_expression(self._current_expression())
                out = evaluate(expr, bars)
            else:
                out = self._dry_compute_python(bars)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Preview failed: {exc}", level="error")
            return
        self._render_preview(bars, out)
        self._set_status(
            f"Preview rendered ({len(bars)} bars).", level="ok",
        )

    def _dry_compute_python(self, bars: Any) -> dict[str, Any]:
        """Exec the user's Python body in a fresh namespace + compute."""
        ns: dict[str, Any] = {}
        body = self._current_python_body()
        exec(compile(body, "<custom>", "exec"), ns)  # noqa: S102
        # Find the registered factory; prefer the one matching our name.
        name = self._name_var.get().strip()
        factory = INDICATORS.get(name)
        if factory is None:
            raise RuntimeError(
                f"Python body did not register an indicator named {name!r}"
            )
        ind = factory()
        if hasattr(ind, "compute_arr"):
            return ind.compute_arr(bars)
        return ind.compute(list(bars.candles or []))

    def _compute_conditions(self, bars: Any) -> dict[str, Any]:
        """Evaluate the current Group tree per-bar against ``bars``.

        Mirrors what the generated module's ``compute_arr`` does so the
        Preview matches what the saved indicator will compute.
        """
        import numpy as np

        from ..scanner.engine import EvaluationContext, IndicatorMemo, evaluate_group

        grp = self._current_group()
        n = int(bars.close.size)
        out_arr = np.full(n, np.nan, dtype=float)
        candles = list(bars.candles or [])
        if not candles or len(candles) != n:
            return {"value": out_arr}
        try:
            warmup = warmup_for_conditions(grp.to_dict())
        except ExpressionError:
            warmup = 0
        memo = IndicatorMemo(candles=candles)
        memo._bars = bars
        ctx = EvaluationContext(
            symbol="<custom>",
            interval=getattr(bars, "interval", None) or "1d",
            bars=bars,
            candles=candles,
            current_index=int(warmup),
            memo=memo,
        )
        for i in range(int(warmup), n):
            ctx.current_index = i
            ctx.evidence = []
            try:
                v = evaluate_group(grp, ctx)
            except Exception:  # noqa: BLE001
                v = None
            if v is True:
                out_arr[i] = 1.0
            elif v is False:
                out_arr[i] = 0.0
        return {"value": out_arr}

    def _render_preview(self, bars: Any, out: dict[str, Any]) -> None:
        # Lazy import to keep dialog construction cheap on systems
        # where the matplotlib Tk backend hasn't been initialised yet.
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        for child in self._preview_frame.winfo_children():
            child.destroy()
        fig = Figure(figsize=(7, 3), dpi=90)
        is_conditions = self._mode_var.get() == _CONDITIONS_MODE
        if self._overlay_var.get():
            ax = fig.add_subplot(111)
            ax.plot(bars.close, color=FALLBACK_GRAY, linewidth=1.0, label="close")
            for key, arr in out.items():
                if is_conditions:
                    ax.step(range(len(arr)), arr, where="post", linewidth=1.4, label=key)
                else:
                    ax.plot(arr, linewidth=1.4, label=key)
            ax.legend(loc="upper left", fontsize=8)
        else:
            ax_price = fig.add_subplot(211)
            ax_price.plot(bars.close, color=FALLBACK_GRAY, linewidth=1.0)
            ax_ind = fig.add_subplot(212, sharex=ax_price)
            for key, arr in out.items():
                if is_conditions:
                    ax_ind.step(range(len(arr)), arr, where="post", linewidth=1.4, label=key)
                else:
                    ax_ind.plot(arr, linewidth=1.4, label=key)
            if is_conditions:
                ax_ind.set_ylim(-0.1, 1.1)
            ax_ind.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=self._preview_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        self._preview_canvas = canvas
        self._set_preview_expanded(True)

    def _reset_preview(self) -> None:
        """Tear down any rendered preview and re-collapse the pane."""
        for child in self._preview_frame.winfo_children():
            child.destroy()
        self._preview_canvas = None
        ttk.Label(
            self._preview_frame,
            text="(Click Preview to render the indicator on the current chart's candles.)",
            foreground=FALLBACK_GRAY,
        ).pack(side="top", anchor="w")
        self._set_preview_expanded(False)

    def _set_preview_expanded(self, expanded: bool) -> None:
        """Toggle whether the Preview pane claims vertical space.

        Collapsed (default) keeps the Composition body — including a
        parameter-heavy Conditions/BlockEditor — at full height. The
        pane expands only once a chart has actually been rendered so an
        empty placeholder never steals room from the editor.
        """
        if expanded == self._preview_expanded:
            return
        self._preview_expanded = expanded
        if expanded:
            self._preview_frame.pack_configure(fill="both", expand=True)
        else:
            self._preview_frame.pack_configure(fill="x", expand=False)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        ok, msg = self._validate()
        if not ok:
            self._set_status(msg, level="error")
            return
        name = self._name_var.get().strip()
        try:
            filename = safe_indicator_filename(name)
        except ExpressionError as exc:
            self._set_status(str(exc), level="error")
            return
        target = self._directory / filename

        # Overwrite confirmation.
        if target.exists() and (
            self._current_path is None or self._current_path != target
        ):
            if not messagebox.askyesno(
                "Overwrite custom indicator",
                f"{filename} already exists. Overwrite?",
                parent=self,
            ):
                return

        # Python-mode security gate.
        if self._mode_var.get() == _PYTHON_MODE:
            if not messagebox.askokcancel(
                "Save Python indicator",
                "This indicator contains custom Python code which will be "
                "executed every time the indicator is computed.\n\n"
                "Only save indicators you trust. Continue?",
                parent=self, icon="warning",
            ):
                return

        # Dry-compute against synthetic bars to surface broken
        # compositions before they land on disk.
        try:
            self._dry_compute_synthetic()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Dry-compute failed: {exc}", level="error")
            return

        created = ""
        if target.exists():
            existing_meta = _read_header_metadata(target)
            created = existing_meta.get("created", "")
        if not created:
            created = _utc_now_iso()
        updated = _utc_now_iso()

        try:
            mode = self._mode_var.get()
            scannable = bool(self._scannable_var.get())
            if mode == _CONDITIONS_MODE:
                source = conditions_to_python(
                    name=name,
                    group_dict=self._current_group().to_dict(),
                    description=self._desc_var.get().strip(),
                    overlay=self._overlay_var.get(),
                    created=created,
                    updated=updated,
                    scannable=scannable,
                )
            elif mode == _EXPRESSION_MODE:
                source = expression_to_python(
                    name=name,
                    expression=self._current_expression(),
                    description=self._desc_var.get().strip(),
                    overlay=self._overlay_var.get(),
                    created=created,
                    updated=updated,
                    scannable=scannable,
                )
            else:
                source = python_mode_wrapper(
                    name=name,
                    body=self._current_python_body(),
                    description=self._desc_var.get().strip(),
                    created=created,
                    updated=updated,
                    scannable=scannable,
                )
        except ExpressionError as exc:
            self._set_status(str(exc), level="error")
            return

        # Atomic write: tempfile in same dir → os.replace.
        try:
            self._atomic_write(target, source)
        except OSError as exc:
            self._set_status(f"Write failed: {exc}", level="error")
            return

        # Drop any prior in-process registration so the new file
        # supersedes it (handles edits + name reuse).
        ind_loader.unregister_indicator(name)
        result = ind_loader.register_user_indicator_file(target)
        if result.errors:
            err = result.errors[0]
            self._set_status(
                f"Saved {filename} but registration failed: {err.error}",
                level="error",
            )
            return

        self._current_path = target
        self._refresh_saved_list()
        try:
            files = self._builder_files()
            for i, p in enumerate(files):
                if p == target:
                    self._listbox.selection_clear(0, "end")
                    self._listbox.selection_set(i)
                    break
        except tk.TclError:
            pass
        self._set_status(
            f"Saved {filename} and registered as {name!r}.", level="ok",
        )

    def _dry_compute_synthetic(self) -> None:
        """Build a tiny synthetic Bars view and run the indicator once."""
        from ..strategy_tester.warmup import _synthetic_bars  # reuse helper

        bars = _synthetic_bars(200)
        mode = self._mode_var.get()
        if mode == _CONDITIONS_MODE:
            out = self._compute_conditions(bars)
        elif mode == _EXPRESSION_MODE:
            expr = parse_expression(self._current_expression())
            out = evaluate(expr, bars)
        else:
            out = self._dry_compute_python(bars)
        if not out:
            raise RuntimeError("compute returned an empty dict")
        for key, arr in out.items():
            try:
                import numpy as np
                a = np.asarray(arr)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"output {key!r} is not array-like: {exc}") from exc
            if a.size == 0:
                raise RuntimeError(f"output {key!r} is empty")
            # Conditions output is naturally NaN-padded during warmup +
            # may be all-NaN if no bars evaluate True/False. Skip the
            # strict finite check for that mode — emptiness is the real
            # failure signal, and we caught that above.
            if mode != _CONDITIONS_MODE and not np.any(np.isfinite(a)):
                raise RuntimeError(f"output {key!r} is all-NaN / empty")

    @staticmethod
    def _atomic_write(target: Path, source: str) -> None:
        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.stem}-", suffix=".tmp", dir=str(directory),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(source)
            os.replace(tmp_path, target)
        except Exception:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------
    def _set_status(self, msg: str, *, level: str = "info") -> None:
        self._status_var.set(msg)
        color = {
            "ok": "#1f7a2f",
            "error": "#a02020",
            "info": "#444444",
        }.get(level, "#444444")
        try:
            self._status_lbl.configure(foreground=color)
        except tk.TclError:
            pass

    def _on_cancel(self) -> None:
        try:
            if self._app is not None and getattr(
                self._app, "_custom_indicator_dialog", None,
            ) is self:
                self._app._custom_indicator_dialog = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        super()._on_cancel()
