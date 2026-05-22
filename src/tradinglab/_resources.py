"""Frozen-bundle-aware resource path resolution.

PyInstaller ``--onedir`` builds extract bundled resources to
``sys._MEIPASS`` (which equals the contents directory ``_internal/``
in PyInstaller 6.x). Source / dev installs see resources under the
repo root. Code that needs to read packaged data (e.g. the entry-
strategy template directory) must use :func:`resource_path` so it
works in both modes.

Layout assumption (mirrored by ``TradingLab.spec``)::

    Source (dev):
        <repo>/data/entry_strategy_templates/*.json
        <repo>/config/example_config.json
        <repo>/.env.example

    Frozen (PyInstaller --onedir):
        <bundle>/TradingLab.exe
        <bundle>/_internal/data/entry_strategy_templates/*.json
        <bundle>/_internal/config/example_config.json
        <bundle>/_internal/.env.example

The helpers below return paths that resolve correctly in either mode.
Callers should treat the returned :class:`Path` as read-only — writing
into ``sys._MEIPASS`` is supported but useless because the directory
is regenerated on every launch.
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """``True`` when running from a PyInstaller-frozen bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_root() -> Path:
    """Return the base directory under which bundled resources live.

    * In a frozen build: ``sys._MEIPASS`` (the contents directory).
    * In a source / dev install: the repo root (parent of ``src/``).

    Both pointers anchor the same logical layout so callers can use
    :func:`resource_path` without conditionals.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    # ``_resources.py`` lives at ``src/tradinglab/_resources.py``.
    # parents[2] therefore equals the repo root.
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """Join ``parts`` onto :func:`resource_root` and return a :class:`Path`.

    Example::

        templates = resource_path("data", "entry_strategy_templates")
        # source: <repo>/data/entry_strategy_templates
        # frozen: <bundle>/_internal/data/entry_strategy_templates
    """
    return resource_root().joinpath(*parts)


__all__ = ["is_frozen", "resource_root", "resource_path"]
