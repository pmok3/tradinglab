from __future__ import annotations

import tkinter as tk

from .. import settings as _settings
from ..data import DATA_SOURCES
from ..watchlists import DEFAULT_WATCHLIST_NAME as _DEFAULT_WATCHLIST_NAME

_DEFAULT_TICKER = "AMD"
_DEFAULT_COMPARE = "SPY"
_DEFAULT_INTERVAL = "1d"


class AppState:
    """Registry for all Tk state variables.

    All variables are parented to ``master`` (the Tk root) so they
    remain valid for the application lifetime. The class itself is
    a plain Python object — not a widget.
    """

    def __init__(self, master: tk.Misc, startup_defaults: dict[str, str]):
        sd = startup_defaults
        self.ha_display = tk.BooleanVar(
            master=master,
            value=bool(_settings.get("heikin_ashi", False)),
        )
        self.highlight_key_bars = tk.BooleanVar(
            master=master,
            value=bool(_settings.get("highlight_key_bars", False)),
        )
        self.highlight_ha_flat = tk.BooleanVar(
            master=master,
            value=bool(_settings.get("highlight_ha_flat", True)),
        )
        self.chartstack_visible = tk.BooleanVar(
            master=master,
            value=self._initial_chartstack_visible(),
        )
        self.ticker = tk.StringVar(master=master, value=sd.get("ticker", _DEFAULT_TICKER))
        self.compare_ticker = tk.StringVar(
            master=master,
            value=sd.get("compare", _DEFAULT_COMPARE),
        )
        self.compare = tk.BooleanVar(master=master, value=False)
        self.compare_enabled = self.compare
        self.compare_label = tk.StringVar(master=master, value="")
        self.source = tk.StringVar(master=master, value=self._resolve_source(sd))
        self.interval = tk.StringVar(master=master, value=sd.get("interval", _DEFAULT_INTERVAL))
        self.prepost = tk.BooleanVar(master=master, value=False)
        self.days = tk.StringVar(master=master, value="")
        self.dark = tk.BooleanVar(master=master, value=sd.get("theme") == "dark")
        self.log_price = tk.BooleanVar(master=master, value=False)
        self.watchlist = tk.StringVar(master=master, value=_DEFAULT_WATCHLIST_NAME)
        self.status = tk.StringVar(master=master, value="")
        self.status_display = tk.StringVar(master=master, value="")
        try:
            self.compare.trace_add("write", self._sync_compare_label)
            self.compare_ticker.trace_add("write", self._sync_compare_label)
        except (AttributeError, tk.TclError):
            pass
        self._sync_compare_label()

    def _sync_compare_label(self, *_args: object) -> None:
        try:
            on = bool(self.compare.get())
            sym = (self.compare_ticker.get() or "").strip().upper()
            self.compare_label.set(sym if on else "")
        except tk.TclError:
            pass

    @staticmethod
    def _initial_chartstack_visible() -> bool:
        try:
            from .chartstack import settings_adapter as _cs_adapter

            return bool(_cs_adapter.is_enabled())
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _resolve_source(startup_defaults: dict[str, str]) -> str:
        source = startup_defaults.get("source", "")
        if source not in DATA_SOURCES:
            return next(iter(DATA_SOURCES), "synthetic")
        return source
