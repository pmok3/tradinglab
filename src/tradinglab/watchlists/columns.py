"""Watchlist column model — **API skeleton (implementation pending)**.

Defines the shape of a configured watchlist column (a ``"system"``
column like ``last``/``next_earn`` or a ``"signal"`` column backed by a
scanner :class:`~tradinglab.scanner.model.FieldRef`) plus its
serialization / validation / header-label surface.

Behavioral functions raise :class:`NotImplementedError` until the
feature is built; the dataclass + constants define the documented shape.
See ``columns.spec.md`` and ``docs/WATCHLIST_COLUMNS.md``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..scanner import fields as _scanner_fields
from ..scanner.model import FieldRef

#: The always-available system columns (today's fixed watchlist columns).
SYSTEM_COLUMN_IDS: tuple[str, ...] = (
    "ticker", "last", "change", "change_pct", "next_earn",
)
KIND_SYSTEM = "system"
KIND_SIGNAL = "signal"

#: ``ticker`` is always first and never removable.
LOCKED_COLUMN_ID = "ticker"


@dataclass(frozen=True)
class WatchlistColumn:
    """One configured column.

    ``kind == "system"`` uses ``id`` from :data:`SYSTEM_COLUMN_IDS`.
    ``kind == "signal"`` carries a scanner ``ref`` (field + params +
    interval; active symbol in v1) and a stable ``id`` derived from it.
    ``fmt`` is a display preset (``auto``/``number:N``/``percent``/
    ``signed_pct``/``multiplier``/``int``/``date``/``glyph``); the raw
    value used for sorting is kept separate from the formatted cell text.
    """

    kind: str
    id: str
    ref: FieldRef | None = None
    label: str = ""
    width: int = 80
    anchor: str = "center"
    fmt: str = "auto"


# System-column display metadata — matches today's ``gui/watchlist_tab._WL_COLUMNS``.
_SYSTEM_DISPLAY: dict[str, tuple[str, int, str]] = {
    "ticker": ("Ticker", 80, "w"),
    "last": ("Last", 80, "center"),
    "change": ("Change", 80, "center"),
    "change_pct": ("Change%", 70, "center"),
    "next_earn": ("Next", 90, "center"),
}

_DAILY_ALIASES = ("", "1d", "1day", "d", "D")


def _interval_tag(interval: str | None) -> str:
    """Compact non-daily interval tag for a header (``""`` for daily)."""
    iv = (interval or "").strip()
    return "" if iv in _DAILY_ALIASES else iv


def _fmt_param(v: object) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def signal_column_id(ref: FieldRef) -> str:
    """Deterministic, unique Treeview column id for a signal ``ref``."""
    key = {
        "id": ref.id,
        "params": dict(ref.params),
        "interval": ref.interval or "",
        "output_key": ref.output_key,
        "symbol": ref.symbol,
    }
    return "sig:" + json.dumps(key, sort_keys=True, separators=(",", ":"))


def default_columns() -> list[WatchlistColumn]:
    """Return the default column set (today's Ticker/Last/Change/Change%/Next)."""
    out: list[WatchlistColumn] = []
    for cid in SYSTEM_COLUMN_IDS:
        label, width, anchor = _SYSTEM_DISPLAY[cid]
        out.append(
            WatchlistColumn(kind=KIND_SYSTEM, id=cid, label=label, width=width, anchor=anchor)
        )
    return out


def column_to_dict(col: WatchlistColumn) -> dict:
    """Serialize one column to a JSON-safe dict."""
    d: dict = {"kind": col.kind, "id": col.id}
    if col.ref is not None:
        d["ref"] = col.ref.to_dict()
    if col.label:
        d["label"] = col.label
    if col.width != 80:
        d["width"] = col.width
    if col.anchor != "center":
        d["anchor"] = col.anchor
    if col.fmt != "auto":
        d["fmt"] = col.fmt
    return d


def column_from_dict(data: object) -> WatchlistColumn | None:
    """Deserialize one column; return ``None`` on malformed / unknown input."""
    if not isinstance(data, Mapping):
        return None
    kind = data.get("kind")
    if kind not in (KIND_SYSTEM, KIND_SIGNAL):
        return None
    ref: FieldRef | None = None
    cid = str(data.get("id", "") or "")
    if kind == KIND_SIGNAL:
        refd = data.get("ref")
        if not isinstance(refd, Mapping):
            return None
        try:
            ref = FieldRef.from_dict(refd)
        except (ValueError, TypeError, KeyError):
            return None
        cid = signal_column_id(ref)
    elif cid not in SYSTEM_COLUMN_IDS:
        return None
    try:
        width = int(data.get("width", 80) or 80)
    except (TypeError, ValueError):
        width = 80
    return WatchlistColumn(
        kind=str(kind),
        id=cid,
        ref=ref,
        label=str(data.get("label", "") or ""),
        anchor=str(data.get("anchor", "center") or "center"),
        width=width,
        fmt=str(data.get("fmt", "auto") or "auto"),
    )


def columns_to_json(cols: list[WatchlistColumn]) -> list[dict]:
    """Serialize an ordered column list."""
    return [column_to_dict(c) for c in cols]


def columns_from_json(data: object) -> list[WatchlistColumn]:
    """Deserialize an ordered column list, tolerating junk."""
    out: list[WatchlistColumn] = []
    if isinstance(data, list):
        for entry in data:
            col = column_from_dict(entry)
            if col is not None:
                out.append(col)
    return validate_columns(out)


def validate_columns(cols: list[WatchlistColumn]) -> list[WatchlistColumn]:
    """Return a valid ordered list: ``ticker`` first + locked, deduped, invalid dropped."""
    seen: set[str] = set()
    kept: list[WatchlistColumn] = []
    for c in cols:
        if c.kind == KIND_SIGNAL:
            if c.ref is None:
                continue
            cid = signal_column_id(c.ref)
            if c.id != cid:
                c = replace(c, id=cid)
        elif c.kind == KIND_SYSTEM:
            if c.id not in SYSTEM_COLUMN_IDS:
                continue
            cid = c.id
        else:
            continue
        if not cid or cid in seen:
            continue
        seen.add(cid)
        kept.append(c)
    ticker = next((c for c in kept if c.id == LOCKED_COLUMN_ID), None)
    if ticker is None:
        label, width, anchor = _SYSTEM_DISPLAY["ticker"]
        ticker = WatchlistColumn(
            kind=KIND_SYSTEM, id=LOCKED_COLUMN_ID, label=label, width=width, anchor=anchor
        )
    others = [c for c in kept if c.id != LOCKED_COLUMN_ID]
    return [ticker, *others]


def header_label(col: WatchlistColumn) -> str:
    """Compact header text, e.g. ``RVOL(20,5m)`` / ``ADX(14,D)`` / ``Chg%``."""
    if col.label:
        return col.label
    if col.kind == KIND_SYSTEM:
        disp = _SYSTEM_DISPLAY.get(col.id)
        return disp[0] if disp else col.id
    ref = col.ref
    if ref is None:
        return col.id
    spec = _scanner_fields.get_field(ref.id)
    base = spec.label if spec is not None else ref.id
    inner: list[str] = []
    for k in sorted(ref.params):
        v = ref.params[k]
        if isinstance(v, bool):
            continue
        inner.append(_fmt_param(v))
    tag = _interval_tag(ref.interval)
    if tag:
        inner.append(tag)
    return f"{base}({','.join(inner)})" if inner else base


__all__ = (
    "SYSTEM_COLUMN_IDS",
    "KIND_SYSTEM",
    "KIND_SIGNAL",
    "LOCKED_COLUMN_ID",
    "WatchlistColumn",
    "signal_column_id",
    "default_columns",
    "column_to_dict",
    "column_from_dict",
    "columns_to_json",
    "columns_from_json",
    "validate_columns",
    "header_label",
)
