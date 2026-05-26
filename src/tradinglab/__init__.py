"""Stock candlestick charting package.

Public entry points:
    ChartApp  — the Tkinter application class (lazy)
    main()    — convenience launcher for ``python -m tradinglab`` (lazy)
    __version__ — semantic version (PEP 440)

``ChartApp`` and ``main`` are loaded lazily via PEP 562 ``__getattr__``
so that ``import tradinglab`` (or ``from tradinglab import __version__``)
doesn't drag in the full GUI stack (matplotlib + Tk + every subsystem
``app.py`` pulls in transitively). This keeps `--version` probes,
test discovery, and non-GUI tooling fast — measured at ~300-800ms
saved on cold start. The first attribute access of ``ChartApp`` or
``main`` triggers the actual import. See audit ``tradinglab-init-lazy``.
"""

from ._version import __version__, version_string

__all__ = ["ChartApp", "main", "__version__", "version_string"]


def __getattr__(name: str):
    """PEP 562 lazy attribute access.

    Loads ``ChartApp`` / ``main`` from ``tradinglab.app`` only when first
    referenced. ``import tradinglab`` no longer triggers matplotlib /
    Tk / numpy import on its own.
    """
    if name in ("ChartApp", "main"):
        from .app import ChartApp, main
        # Cache on the module so subsequent lookups don't pay __getattr__
        # overhead and so ``hasattr(tradinglab, "ChartApp")`` still works
        # the way callers expect.
        globals()["ChartApp"] = ChartApp
        globals()["main"] = main
        return ChartApp if name == "ChartApp" else main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose lazy attributes to ``dir(tradinglab)`` + auto-complete."""
    return sorted(set(globals()) | set(__all__))
