"""Custom watchlists.

Data layer only — no UI wiring in this revision. The app can surface
watchlists in a future iteration (sidebar listbox, right-click →
"Compare with", etc.) by consuming :class:`WatchlistManager`.

Public API::

    Watchlist                  — dataclass(name: str, tickers: List[str])
    WatchlistManager           — load/create/delete/rename/add/remove with
                                 auto-persist to JSON under the cache dir
    load_all / save_all        — low-level storage accessors (mostly for tests)
    DEFAULT_WATCHLIST_NAME     — canonical default watchlist name
    DEFAULT_WATCHLIST_TICKERS  — canonical default starter ticker tuple

The default ``NAME`` / ``TICKERS`` constants live here (single source
of truth) rather than at the call sites. Pre-2026-05 both
``tradinglab.app`` and ``tradinglab.gui.watchlist_tab`` carried local
copies of these constants; the ``app.py`` copy was actually dead and
the ``watchlist_tab`` copy was the only one consumed by
``_ensure_default_watchlist``. Audit ``default-watchlist-fresh``.
"""

from .manager import WatchlistManager
from .storage import Watchlist, export_to_file, import_from_file, load_all, save_all

DEFAULT_WATCHLIST_NAME: str = "Default"
DEFAULT_WATCHLIST_TICKERS: tuple[str, ...] = ("AMD", "NVDA", "INTC", "AAPL", "MSFT")

__all__ = [
    "Watchlist",
    "WatchlistManager",
    "load_all",
    "save_all",
    "export_to_file",
    "import_from_file",
    "DEFAULT_WATCHLIST_NAME",
    "DEFAULT_WATCHLIST_TICKERS",
]
