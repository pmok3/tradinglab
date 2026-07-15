from __future__ import annotations

import os
import tkinter as tk

from .. import defaults as _defaults
from .. import settings as _settings
from ..data import DATA_SOURCES, is_internal_source, user_visible_sources
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
            value=bool(_settings.get("highlight_ha_flat", False)),
        )
        try:
            volume_tod_enabled = bool(_defaults.get("volume_tod_enabled"))
        except Exception:  # noqa: BLE001
            volume_tod_enabled = False
        self.volume_tod = tk.BooleanVar(
            master=master,
            value=volume_tod_enabled,
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
        """Resolve the active data source from persisted startup defaults.

        Precedence: the ``TRADINGLAB_STARTUP_SOURCE`` env override (a power-user
        knob + the test seam that keeps ChartApp-boot tests on a deterministic,
        network-free source despite the ``"Auto"`` default) wins first, when it
        names a registered non-internal source. Otherwise the persisted value is
        used, demoting to the first user-visible source if it is missing,
        unregistered, or flagged internal (e.g. an old settings.json from when
        synthetic was user-selectable). Final fallback is the literal
        ``"yfinance"`` so a degenerate DATA_SOURCES (empty in unit tests) doesn't
        crash app startup.
        """
        env_src = os.environ.get("TRADINGLAB_STARTUP_SOURCE", "").strip()
        if env_src and env_src in DATA_SOURCES and not is_internal_source(env_src):
            return env_src
        source = startup_defaults.get("source", "")
        if not source or source not in DATA_SOURCES or is_internal_source(source):
            visible = user_visible_sources()
            return visible[0] if visible else "yfinance"
        return source
