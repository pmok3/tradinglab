"""Market-internals ("Quant") catalog and supporting data.

See :mod:`tradinglab.quant.catalog` for the row definitions rendered by
the **Quant** side tab (``gui/quant_tab.py``).
"""
from __future__ import annotations

from .catalog import (
    QUANT_CATALOG,
    UNAVAILABLE_SYMBOL_TEXT,
    QuantGroup,
    QuantRow,
    available_rows,
    available_symbols,
    iter_rows,
    row_for_key,
)

__all__ = [
    "QUANT_CATALOG",
    "UNAVAILABLE_SYMBOL_TEXT",
    "QuantGroup",
    "QuantRow",
    "available_rows",
    "available_symbols",
    "iter_rows",
    "row_for_key",
]
