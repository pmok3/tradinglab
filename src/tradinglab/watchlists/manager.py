"""In-memory watchlist manager with explicit-save semantics.

Behavior change (was: auto-persisting on every CRUD op):

- `WatchlistManager()` starts **empty**. Nothing is read from disk on
  construction. Existing data in
  ``%LOCALAPPDATA%\\tradinglab\\watchlists.json`` is left alone but
  not auto-loaded.
- All CRUD operations mutate in-memory state and flip an internal
  ``_dirty`` flag. They do **not** write to disk.
- Users explicitly load a watchlist file via :meth:`load_from_file` and
  save via :meth:`save_to_file` (text-editor model). The ``ChartApp``
  exposes these as ``File → Load Watchlists…`` / ``Save Watchlists`` /
  ``Save Watchlists As…`` menu items, parallel to the configuration
  load/save flow.
- The Watchlists dialog's existing ``Import…`` / ``Export…`` buttons
  remain functional and route through the same load/save methods so
  there's a single dirty-tracking source of truth.

The manager is not thread-safe — call it from the UI thread only.
"""

from __future__ import annotations

from pathlib import Path

from .storage import (
    Watchlist,
    normalize_tickers,
)
from .storage import (
    export_to_file as _export_to_file,
)
from .storage import (
    import_from_file as _import_from_file,
)


class WatchlistManager:
    # Class-level fallback. Instance attribute (``self.MAX_PINNED``)
    # is the one the rest of the system reads — it's seeded in
    # ``__init__`` from the ``watchlist_max_pinned`` Tunable so users
    # can lift the cap from Settings without recompiling. Audit
    # ``pinned-watchlist-cap``.
    MAX_PINNED: int = 5

    def __init__(self) -> None:
        # Start empty — no auto-load. See module docstring for rationale.
        self._items: dict[str, Watchlist] = {}
        self._pinned: list[str] = []
        self._dirty: bool = False
        self._loaded_path: Path | None = None
        # Resolve the per-instance cap from the persisted tunable so
        # later mutations of the global tunable (settings reload
        # mid-session) don't surprise a manager that's already in
        # use. A corrupt / missing read falls back to the class-level
        # default so the watchlist UI never gates startup. Audit
        # ``pinned-watchlist-cap``.
        try:
            from .. import defaults as _defaults
            cap = int(_defaults.get("watchlist_max_pinned"))
            if cap < 1:
                cap = WatchlistManager.MAX_PINNED
            self.MAX_PINNED = cap
        except Exception:  # noqa: BLE001
            self.MAX_PINNED = WatchlistManager.MAX_PINNED

    # --- reads ---------------------------------------------------------
    def list_names(self) -> list[str]:
        return list(self._items.keys())

    def all(self) -> list[Watchlist]:
        return list(self._items.values())

    def get(self, name: str) -> Watchlist | None:
        return self._items.get(name)

    def pinned_names(self) -> list[str]:
        """Return pinned names in UI order (left-to-right sub-tabs)."""
        return list(self._pinned)

    # --- dirty / loaded-path tracking ---------------------------------
    def is_dirty(self) -> bool:
        """True if mutations have occurred since the last load/save."""
        return self._dirty

    def loaded_path(self) -> Path | None:
        """Most recently loaded/saved file, or None if never loaded."""
        return self._loaded_path

    def clear(self) -> None:
        """Wipe all in-memory state (watchlists, pins, dirty, loaded path)."""
        self._items.clear()
        self._pinned.clear()
        self._dirty = False
        self._loaded_path = None

    # --- writes (in-memory; mark dirty) ------------------------------
    def create(self, name: str, tickers: list[str] | None = None) -> Watchlist:
        if name in self._items:
            raise ValueError(f"Watchlist '{name}' already exists")
        wl = Watchlist(name=name, tickers=normalize_tickers(tickers))
        self._items[name] = wl
        self._dirty = True
        return wl

    def delete(self, name: str) -> bool:
        if name not in self._items:
            return False
        del self._items[name]
        if name in self._pinned:
            self._pinned.remove(name)
        self._dirty = True
        return True

    def rename(self, old: str, new: str) -> None:
        if old not in self._items:
            raise KeyError(old)
        if new in self._items and new != old:
            raise ValueError(f"Watchlist '{new}' already exists")
        wl = self._items.pop(old)
        wl.name = new
        self._items[new] = wl
        if old in self._pinned:
            idx = self._pinned.index(old)
            self._pinned[idx] = new
        self._dirty = True

    def add_ticker(self, name: str, ticker: str) -> None:
        wl = self._require(name)
        t = ticker.strip().upper()
        if t and t not in wl.tickers:
            wl.tickers.append(t)
            self._dirty = True

    def remove_ticker(self, name: str, ticker: str) -> None:
        wl = self._require(name)
        t = ticker.strip().upper()
        if t in wl.tickers:
            wl.tickers.remove(t)
            self._dirty = True

    def set_tickers(self, name: str, tickers: list[str]) -> None:
        wl = self._require(name)
        wl.tickers = normalize_tickers(tickers)
        self._dirty = True

    # --- pin management -----------------------------------------------
    def pin(self, name: str) -> None:
        if name not in self._items:
            raise KeyError(name)
        if name in self._pinned:
            return
        if len(self._pinned) >= self.MAX_PINNED:
            raise ValueError(
                f"Cannot pin more than {self.MAX_PINNED} watchlists")
        self._pinned.append(name)
        self._dirty = True

    def unpin(self, name: str) -> None:
        if name in self._pinned:
            self._pinned.remove(name)
            self._dirty = True

    def reorder_pins(self, names: list[str]) -> None:
        if sorted(names) != sorted(self._pinned):
            raise ValueError(
                "reorder_pins requires a permutation of the current pins")
        if len(names) != len(set(names)):
            raise ValueError("reorder_pins: duplicate names")
        self._pinned = list(names)
        self._dirty = True

    # --- bulk import (in-memory) --------------------------------------
    def import_watchlists(
        self,
        incoming: list[Watchlist],
        *,
        mode: str = "merge",
        pinned: list[str] | None = None,
    ) -> int:
        """Add / overwrite watchlists from an external file.

        ``mode="merge"`` keeps existing watchlists; incoming entries with
        the same name overwrite. ``mode="replace"`` drops everything
        first (including pins). Returns the count of watchlists written.

        ``pinned`` is the *imported file's* pin list, surfaced through
        :func:`watchlists.storage.import_from_file`. When supplied and
        non-empty, those names are appended to the in-memory pin list
        (after the merge-mode mutation) — preserving prior pins in
        ``mode="merge"`` while honouring the imported file's pin
        ordering for any new pins it brought along. Duplicates are
        de-duped (case-sensitive name match) and the total is capped at
        :attr:`MAX_PINNED`. Names that don't exist after the merge are
        silently dropped (same invariant as :meth:`load_from_file`).

        Pins belonging to lists that no longer exist after the import
        are dropped silently. If the end state has watchlists but no
        pins (e.g. ``mode="replace"`` with an unpinned incoming set
        and no ``pinned`` argument), the first list's name is
        auto-seeded as the sole pin so the UI never appears with zero
        tabs. Always marks the manager dirty (caller decides when to
        persist).
        """
        if mode == "replace":
            self._items.clear()
            self._pinned.clear()
        elif mode != "merge":
            raise ValueError(f"unknown mode: {mode}")
        for wl in incoming:
            self._items[wl.name] = Watchlist(name=wl.name, tickers=list(wl.tickers))
        # Carry over the imported pins (post-merge, de-duped, capped).
        if pinned:
            for name in pinned:
                if (
                    name in self._items
                    and name not in self._pinned
                    and len(self._pinned) < self.MAX_PINNED
                ):
                    self._pinned.append(name)
        # Prune pins for deleted lists; auto-seed first list if empty.
        self._pinned = [n for n in self._pinned if n in self._items]
        if not self._pinned and self._items:
            self._pinned.append(next(iter(self._items)))
        self._dirty = True
        return len(incoming)

    # --- explicit file I/O --------------------------------------------
    def load_from_file(self, path) -> int:
        """Replace in-memory state with the contents of ``path``.

        Mirrors ``settings.import_from_file`` semantics: on success
        replaces ``_items`` and ``_pinned`` wholesale, sets
        ``loaded_path`` to ``path``, and resets dirty to False. Pins
        from the file are honored (clamped to ``MAX_PINNED`` and
        filtered to existing names). Raises on unreadable/malformed
        input — the caller surfaces the error.
        """
        p = Path(path) if not isinstance(path, Path) else path
        incoming, pinned = _import_from_file(p)
        self._items = {w.name: w for w in incoming}
        # Sanitize pins.
        self._pinned = []
        for n in pinned:
            if n in self._items and n not in self._pinned:
                self._pinned.append(n)
            if len(self._pinned) >= self.MAX_PINNED:
                break
        # If the file has lists but no pin survived, seed first one so
        # the UI has a visible sub-tab. Mirrors v1 migration behavior.
        if not self._pinned and self._items:
            self._pinned.append(next(iter(self._items)))
        self._loaded_path = p
        self._dirty = False
        return len(self._items)

    def save_to_file(self, path) -> None:
        """Write current state to ``path`` (creates parent dirs).

        Resets dirty to False and updates ``loaded_path`` on success.
        Raises on I/O error so the caller can show a real message.
        """
        p = Path(path) if not isinstance(path, Path) else path
        p.parent.mkdir(parents=True, exist_ok=True)
        _export_to_file(list(self._items.values()), p, list(self._pinned))
        self._loaded_path = p
        self._dirty = False

    # --- internals -----------------------------------------------------
    def _require(self, name: str) -> Watchlist:
        wl = self._items.get(name)
        if wl is None:
            raise KeyError(name)
        return wl
