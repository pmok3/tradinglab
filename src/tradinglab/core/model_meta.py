"""Shared provenance metadata for saved user records.

``CreatedWith`` records which build produced or last edited a saved
strategy / scan / run config. Three subsystems (`entries`, `exits`,
`scanner`) each shipped their own copy, and the copies **drifted**:

* `entries` grew a ``template: bool`` field (marking prepackaged catalog
  entries) with conditional serialization; `exits` and `scanner` did not.
* `scanner` defaults ``version`` to ``""`` while `entries` / `exits`
  default to ``"0.0.0"``.

The first divergence was not cosmetic — it silently **lost data**. All 20
shipped exit templates carry ``"created_with": {"template": true, ...}`` on
disk, but ``exits.CreatedWith.from_dict`` only read ``app`` and ``version``,
so ``ExitStrategy.from_dict(raw).to_dict()`` dropped the marker. Any
load-then-save of an exit template demoted it to a non-template.

This module is the single definition. ``template`` is serialized **only when
True**, so records that never set it round-trip byte-identically to before.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["CreatedWith"]


@dataclass
class CreatedWith:
    """Audit metadata identifying the build that created/edited a record.

    ``template`` marks a prepackaged catalog entry (shipped under
    ``data/*_templates/``) as opposed to a user-authored record.
    """

    app: str = "tradinglab"
    version: str = "0.0.0"
    template: bool = False

    def to_dict(self) -> dict[str, Any]:
        # ``template`` is omitted when False so records that never set it
        # keep their historical on-disk shape exactly.
        out: dict[str, Any] = {"app": self.app, "version": self.version}
        if self.template:
            out["template"] = True
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CreatedWith:
        defaults = cls()
        return cls(
            app=str(d.get("app", defaults.app)),
            version=str(d.get("version", defaults.version)),
            template=bool(d.get("template", False)),
        )
