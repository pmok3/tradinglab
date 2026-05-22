"""Stock candlestick charting package.

Public entry points:
    ChartApp  — the Tkinter application class
    main()    — convenience launcher (`python -m tradinglab`)
    __version__ — semantic version (PEP 440)
"""

from ._version import __version__, version_string
from .app import ChartApp, main

__all__ = ["ChartApp", "main", "__version__", "version_string"]
