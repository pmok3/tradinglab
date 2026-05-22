"""Setup-tag taxonomy for the sandbox (Phase 1c).

A *setup tag* labels the trade pattern the user thinks they're playing
("breakout", "pullback", "reversal", …). The pre-trade form surfaces
the current tag list as a Combobox; the user can also type a freeform
tag. The post-trade review screen records which tag was used so a
later performance view (Phase 1d) can aggregate by tag.

Phase 1c keeps the store in memory only — the tag list resets every
time the app launches. Persistence (alongside watchlists / settings)
is deferred to Phase 1d so we don't add a JSON-file migration
dependency yet.
"""

from __future__ import annotations

from typing import Iterable, List

_DEFAULT_TAGS: List[str] = [
    "breakout",
    "pullback",
    "reversal",
    "range",
    "news",
    # Event-proximity tags auto-suggested by SandboxController.submit_order
    # when the trade falls within earnings_window_days of an earnings print,
    # or on / immediately after an ex-dividend or special-dividend date.
    # Trader-facing labels; rendering is identical to other tags.
    "earnings_pre_print",
    "earnings_post_print",
    "ex_div_day",
    "post_special_div",
]


class TagStore:
    """Tiny in-memory list with normalised (case-folded) uniqueness.

    Tags are stored case-folded (e.g. ``"BreakOut"`` -> ``"breakout"``)
    to prevent silent duplicates. Order is insertion order — the
    pre-trade form's Combobox will surface the user's curated order.
    """

    def __init__(self, initial: Iterable[str] = ()) -> None:
        self._tags: List[str] = []
        self.replace(list(initial) or list(_DEFAULT_TAGS))

    @staticmethod
    def _norm(tag: str) -> str:
        return str(tag).strip().casefold()

    def list(self) -> List[str]:
        return list(self._tags)

    def add(self, tag: str) -> bool:
        """Append ``tag`` to the end. Returns False if it was a duplicate."""
        t = self._norm(tag)
        if not t:
            return False
        if any(self._norm(x) == t for x in self._tags):
            return False
        self._tags.append(t)
        return True

    def remove(self, tag: str) -> bool:
        t = self._norm(tag)
        for i, existing in enumerate(self._tags):
            if self._norm(existing) == t:
                self._tags.pop(i)
                return True
        return False

    def replace(self, tags: Iterable[str]) -> None:
        """Wholesale replace. Used by the editor dialog's OK button."""
        seen = set()
        new_list: List[str] = []
        for t in tags:
            n = self._norm(t)
            if not n or n in seen:
                continue
            seen.add(n)
            new_list.append(n)
        self._tags = new_list


__all__ = ("TagStore",)
