"""View-menu heatmap handlers (ChartApp mixin).

Holds the two View-menu entries that open a market heatmap:

* :meth:`ViewMenuMixin._on_view_heatmap` — hands off to Finviz in the
  user's browser.
* :meth:`ViewMenuMixin._on_view_live_heatmap` — opens the in-app live
  heatmap window.

They live together because they are the same user intent reached two
ways, and a reader comparing them should not have to hold two files in
their head.

Mixin rules (§7.24):

1. NO ``__init__``, NO ``super().__init__()`` — this is a method bag;
   all instance state is owned by ``ChartApp.__init__``.
2. Inserted alphabetically in the ``ChartApp`` MRO.
3. ``tk.Tk`` stays last.

See ``gui/view_menu.spec.md``.
"""

from __future__ import annotations

import webbrowser
from tkinter import messagebox

#: Finviz S&P 500 **sector** performance treemap (1D). The per-stock
#: 500-square view (``t=sec_all``) is one query-string flip away; the
#: sector view is the more useful glance mid-session.
FINVIZ_HEATMAP_URL = "https://finviz.com/map.ashx?t=sec"


class ViewMenuMixin:
    """View-menu heatmap commands."""

    def _on_view_heatmap(self) -> None:
        """View menu callback: open the Finviz S&P 500 sector heatmap.

        Direct browser launch (no intermediate popup) per the
        ``view-heatmap-launcher`` audit. Mirrors the
        :meth:`gui.help_menu.HelpMenuMixin._on_help_view_online_docs`
        pattern: ``webbrowser.open(url, new=2, autoraise=True)`` with
        a ``messagebox.showinfo`` fallback that surfaces the URL so
        the user can copy-paste it manually when the OS browser
        hand-off fails (locked-down profile / no default browser
        configured / headless run).
        """
        url = FINVIZ_HEATMAP_URL
        try:
            opened = webbrowser.open(url, new=2, autoraise=True)
        except Exception:  # noqa: BLE001
            opened = False
        if opened:
            return
        messagebox.showinfo(
            "Heatmap",
            f"Could not launch a web browser automatically.\n\n"
            f"Open this URL manually:\n{url}",
            parent=self,
        )

    def _on_view_live_heatmap(self) -> None:
        """View menu callback: open the in-app **live** market heatmap.

        The in-app counterpart to the Finviz launcher. Where that one
        hands the trader to a browser showing somebody else's universe,
        this renders the same treemap from the app's own data, on the
        app's own clock, with click-to-chart wired to the primary chart.

        Independent of sandbox: it uses a wall-clock context and, when a
        quote source is configured, a streaming feed rather than REST
        polling — a 500-name map on a polling loop would consume the
        request budget that on-demand chart loads depend on.
        """
        from .sandbox_heatmap import open_live_heatmap

        try:
            open_live_heatmap(self)
        except Exception:  # noqa: BLE001
            messagebox.showinfo(
                "Live Market Heatmap",
                "Could not open the live heatmap.",
                parent=self,
            )


__all__ = ("ViewMenuMixin", "FINVIZ_HEATMAP_URL")
