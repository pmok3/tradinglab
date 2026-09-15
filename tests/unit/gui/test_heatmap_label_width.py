"""Heatmap wrap width excludes ttk padding instead of relying on one theme."""
from types import SimpleNamespace

from tests._window_width import assert_window_width, enlarged_fonts, mapped_window
from tradinglab.backtest.heatmap_provider import HeatmapProvider
from tradinglab.gui.sandbox_heatmap import SandboxHeatmapWindow


def test_heatmap_labels_reserve_theme_insets_at_minimum_width(root, tmp_path):
    provider = HeatmapProvider(meta={}, shares_fetcher=lambda _: [], cache_dir=tmp_path)
    controller = SimpleNamespace(clock_ts=lambda: None, blind=False)
    with enlarged_fonts(root), mapped_window(root):
        window = SandboxHeatmapWindow(root, controller, provider=provider, price_source=lambda *_: (None, None))
        try:
            window.geometry(f"{window.minsize()[0]}x800")
            for label in (window._header, window._status, window._footer):
                label.configure(padding=(3, 0), text="Market Heatmap - AAPL MSFT - regular session "
                                "price and volume detail with additional words to wrap")
            with mapped_window(window):
                assert_window_width(window)
                for label in (window._header, window._status, window._footer):
                    assert label.winfo_reqwidth() <= label.winfo_width()
                    assert int(label.cget("wraplength")) <= label.winfo_width() - 6
        finally:
            window.close()
