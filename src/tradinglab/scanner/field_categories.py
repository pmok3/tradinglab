"""Concept categories for the field catalog — navigation aid for pickers.

The raw field pickers in the condition builder present every builtin (30+)
or indicator in a single flat dropdown. This module groups those fields by
concept so the picker can render a **categorized** dropdown (headers +
members) instead of one long alphabetical list — the "don't throw every
building block at me up front" ask.

Pure module: no Tk. It reads the field catalog
(:func:`tradinglab.scanner.fields.all_fields`) and assigns each field a
category. The GUI (`gui/scanner_block_editor._FieldRefPicker`) consumes
:func:`grouped_combo_values` / :func:`is_category_header`.

Category assignment is **rule-based for builtins** (prefix / membership,
so new builtins land sensibly without edits) and a **small map for
indicators** (unknown / user-plugin indicators fall to ``Other``).
"""
from __future__ import annotations

from .fields import all_fields

# ---------------------------------------------------------------------------
# Categories (ordered — drives dropdown section order)
# ---------------------------------------------------------------------------

CAT_PRICE = "Price & Volume"
CAT_SESSION = "Session"
CAT_TREND = "Trend"
CAT_MOMENTUM = "Momentum"
CAT_VOLUME = "Volume"
CAT_VOLATILITY = "Volatility"
CAT_HEIKIN = "Heikin-Ashi"
CAT_KEYBARS = "Key Bars"
CAT_OTHER = "Other"

FIELD_CATEGORIES: tuple[str, ...] = (
    CAT_PRICE,
    CAT_SESSION,
    CAT_TREND,
    CAT_MOMENTUM,
    CAT_VOLUME,
    CAT_VOLATILITY,
    CAT_HEIKIN,
    CAT_KEYBARS,
    CAT_OTHER,
)

# Built-in indicator kind_id → category. User-plugin / custom indicators
# not listed here resolve to ``CAT_OTHER`` (fail-open, still reachable).
_INDICATOR_CATEGORY: dict[str, str] = {
    "sma": CAT_TREND,
    "ema": CAT_TREND,
    "vwap": CAT_TREND,
    "avwap": CAT_TREND,
    "adx": CAT_TREND,
    "rsi": CAT_MOMENTUM,
    "smi": CAT_MOMENTUM,
    "lrsi": CAT_MOMENTUM,
    "bbands": CAT_VOLATILITY,
    "atr": CAT_VOLATILITY,
    "rvol": CAT_VOLUME,
    "rrvol": CAT_VOLUME,
}

_SESSION_BUILTINS: frozenset[str] = frozenset(
    {"hod", "lod", "time_of_day", "bars_since_open"}
)

# Combobox section-header decoration. The prefix is a distinctive glyph
# (horizontal-bar ``\u2015``) that no field id starts with, so
# :func:`is_category_header` can recognise a header purely by prefix.
_HEADER_PREFIX = "\u2015\u2015 "   # "―― "
_HEADER_SUFFIX = " \u2015\u2015"   # " ――"


def category_of(field_id: str, kind: str) -> str:
    """Return the concept category for ``field_id`` of the given ``kind``.

    ``kind`` is ``"builtin"`` or ``"indicator"``. Builtins are classified
    by rule (Heikin-Ashi / Key Bars / Session / Price & Volume);
    indicators by the built-in map, defaulting to ``Other``.
    """
    fid = str(field_id)
    if kind == "indicator":
        return _INDICATOR_CATEGORY.get(fid, CAT_OTHER)
    # builtin
    if fid.startswith("ha_"):
        return CAT_HEIKIN
    if "key_bar" in fid:
        return CAT_KEYBARS
    if fid in _SESSION_BUILTINS:
        return CAT_SESSION
    return CAT_PRICE


def grouped_field_ids(kind: str) -> list[tuple[str, list[str]]]:
    """Group all fields of ``kind`` into ``(category, sorted_ids)`` pairs.

    Ordered by :data:`FIELD_CATEGORIES`; empty categories are omitted;
    ids within a category are sorted case-insensitively.
    """
    buckets: dict[str, list[str]] = {}
    for spec in all_fields():
        if spec.kind != kind:
            continue
        buckets.setdefault(category_of(spec.id, kind), []).append(str(spec.id))
    out: list[tuple[str, list[str]]] = []
    for cat in FIELD_CATEGORIES:
        ids = buckets.get(cat)
        if ids:
            out.append((cat, sorted(ids, key=str.casefold)))
    return out


def _header(category: str) -> str:
    return f"{_HEADER_PREFIX}{category}{_HEADER_SUFFIX}"


def grouped_combo_values(kind: str) -> tuple[tuple[str, ...], frozenset[str]]:
    """Return ``(values, headers)`` for a categorized readonly Combobox.

    ``values`` interleaves a non-selectable section header before each
    category's members (``["―― Price & Volume ――", "close", "high", …,
    "―― Session ――", "hod", …]``). ``headers`` is the set of header
    strings so the caller can reject a header selection. Members within a
    category keep their raw field id as the value (unchanged commit path).
    """
    values: list[str] = []
    headers: list[str] = []
    for category, ids in grouped_field_ids(kind):
        head = _header(category)
        headers.append(head)
        values.append(head)
        values.extend(ids)
    return tuple(values), frozenset(headers)


def is_category_header(value: str) -> bool:
    """True if ``value`` is a section-header row (not a real field id)."""
    return str(value).startswith(_HEADER_PREFIX)


__all__ = (
    "FIELD_CATEGORIES",
    "category_of",
    "grouped_field_ids",
    "grouped_combo_values",
    "is_category_header",
)
