"""User-saved custom themes — JSON-per-file persistence under app data.

Lets the user capture the current theme override state under a name
and re-apply / delete it later from the Theme Editor. Themes are
persisted as one JSON file per theme under
``<app_data_dir>/themes/<slug>.json`` so they:

* survive uninstall/reinstall (live next to ``settings.json`` in the
  same protected per-user location);
* are easy to back up / share (one file = one theme; copy + paste);
* can be hand-edited (the schema is intentionally simple — three
  top-level keys).

On-disk shape::

    {
      "label": "My Custom Dark",
      "mode":  "dark",
      "overrides": {
        "win_bg":    "#202020",
        "ax_bg":     "#303030",
        "text":      "#f0f0f0",
        "grid":      "#555555",
        "bull_row_bg":"#114433",
        "bear_row_bg":"#441111"
      }
    }

Filename is the slugified label (spaces → ``_``, unsafe chars
dropped). Two themes with labels that slugify to the same filename
are NOT supported — the second ``save_theme`` call overwrites the
first, matching the "save = overwrite" UX users already expect from
the built-in preset Save and Close pattern.

Public surface
==============

* :class:`UserTheme` — dataclass: ``label`` / ``mode`` /
  ``overrides``. The constructor filters ``overrides`` to
  :data:`tradinglab.constants.CUSTOMIZABLE_THEME_KEYS` (safe to
  merge downstream).
* :func:`themes_dir` — lazy resolver for the storage directory.
* :func:`save_theme(theme)` — write a theme atomically.
* :func:`load_all() -> list[UserTheme]` — list every saved theme,
  alphabetically by label. Corrupt files are logged and skipped.
* :func:`delete_theme(label) -> bool` — remove a saved theme by
  label. Returns True iff a file was removed.
* :func:`theme_exists(label) -> bool` — predicate.

The Theme Editor integration adds a "Custom themes" row that lets
the user: pick a saved theme from a combobox + Apply, save the
current theme under a new name, or delete the selected saved theme.
See ``gui/theme_editor.py`` for the UI side.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..constants import CUSTOMIZABLE_THEME_KEYS
from ..core.io_helpers import atomic_write_json, read_json

LOG = logging.getLogger(__name__)

_THEMES_SUBDIR = "themes"
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def themes_dir() -> Path:
    """Return the directory where user themes are persisted.

    Defaults to ``<app_data_dir>/themes/``. Tests monkeypatch this
    helper to redirect at a ``tmp_path`` (the docstring on the test
    fixture shows the pattern).
    """
    from ..paths import app_data_dir
    return app_data_dir() / _THEMES_SUBDIR


def _slugify_label(label: str) -> str:
    """Map a user-supplied label to a safe filesystem stem.

    * Strip leading/trailing whitespace.
    * Replace runs of whitespace with a single ``_``.
    * Drop any character outside ``[A-Za-z0-9_-]`` (NB: dots are
      excluded too — they're filesystem-valid but allowing them
      enables labels like ``../etc/passwd`` to round-trip into
      ``..etc..passwd`` filenames which are confusing at minimum
      and a path-traversal smell at worst).
    * Collapse to the literal ``"theme"`` when everything is stripped
      so we never produce an empty filename (which would land at
      ``<dir>/.json`` and confuse glob / delete logic).
    """
    if not isinstance(label, str):
        label = str(label)
    s = label.strip()
    s = re.sub(r"\s+", "_", s)
    s = _FILENAME_SAFE_RE.sub("", s)
    return s or "theme"


def _path_for_label(label: str) -> Path:
    return themes_dir() / f"{_slugify_label(label)}.json"


@dataclass(frozen=True)
class UserTheme:
    """One user-saved theme.

    ``label`` is the UI text the user typed; ``mode`` is ``"light"``
    or ``"dark"``; ``overrides`` is filtered to
    :data:`CUSTOMIZABLE_THEME_KEYS` on construction so a hand-edited
    JSON can't smuggle unknown keys into downstream merges.
    """

    label: str
    mode: str
    overrides: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in ("light", "dark"):
            raise ValueError(
                f"UserTheme.mode must be 'light' or 'dark'; got {self.mode!r}",
            )
        allowed = {k for k, _ in CUSTOMIZABLE_THEME_KEYS}
        filtered = {
            k: v
            for k, v in (self.overrides or {}).items()
            if k in allowed and isinstance(v, str)
        }
        # frozen dataclass workaround — bypass setattr.
        object.__setattr__(self, "overrides", filtered)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "mode": self.mode,
            "overrides": dict(self.overrides),
        }

    @classmethod
    def from_dict(cls, data: dict) -> UserTheme:
        """Parse a UserTheme from a JSON-decoded dict.

        Raises ``KeyError`` / ``ValueError`` on malformed input so
        :func:`load_all` can catch and skip a single bad file
        without aborting the whole scan.
        """
        if not isinstance(data, dict):
            raise ValueError(f"expected dict, got {type(data).__name__}")
        return cls(
            label=str(data["label"]),
            mode=str(data["mode"]),
            overrides=dict(data.get("overrides", {})),
        )


def save_theme(theme: UserTheme) -> Path:
    """Persist ``theme`` to ``<themes_dir>/<slug>.json``.

    Overwrites any existing file with the same slug (themes are
    identified by label / slug — "save the same name twice" is
    "update the saved theme"). Creates the themes dir if missing.
    """
    target = _path_for_label(theme.label)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, theme.to_dict())
    return target


def theme_exists(label: str) -> bool:
    """True iff a theme with this label is already on disk."""
    return _path_for_label(label).is_file()


def delete_theme(label: str) -> bool:
    """Remove the saved theme matching ``label``.

    Returns ``True`` iff a file was removed, ``False`` if no theme
    by that name existed.
    """
    p = _path_for_label(label)
    if not p.is_file():
        return False
    try:
        p.unlink()
        return True
    except OSError as exc:
        LOG.warning("theme_store: failed to delete %s: %s", p, exc)
        return False


def load_all() -> list[UserTheme]:
    """Return every saved theme, alphabetically by label.

    Corrupt JSON / missing keys log a warning and skip the file —
    one bad file should never block the rest of the user's themes
    from showing up in the dropdown.
    """
    d = themes_dir()
    if not d.is_dir():
        return []
    out: list[UserTheme] = []
    for path in sorted(d.glob("*.json")):
        raw = read_json(path, default=None, log=LOG, log_label="theme_store")
        if raw is None:
            continue
        try:
            out.append(UserTheme.from_dict(raw))
        except (KeyError, ValueError, TypeError) as exc:
            LOG.warning(
                "theme_store: skipping malformed theme %s: %s", path.name, exc,
            )
    return sorted(out, key=lambda t: t.label.lower())


__all__ = (
    "UserTheme",
    "themes_dir",
    "save_theme",
    "theme_exists",
    "delete_theme",
    "load_all",
)
