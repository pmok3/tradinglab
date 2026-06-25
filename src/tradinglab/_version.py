"""Single source of truth for the package version.

This file is read by:

* :mod:`tradinglab.__init__` (re-exports ``__version__``).
* ``pyproject.toml`` (via ``[tool.setuptools.dynamic]``) so wheel /
  sdist / ``pip install -e .`` all pick up the same number.
* ``tools/bump_version.py`` (writes the ``__version__`` line).
* ``tools/build_exe.ps1`` (reads the version, drops a sibling
  ``_build_info.py`` file with the git commit + build date).

Format: ``MAJOR.MINOR.PATCH`` (PEP 440 compatible).

The ``BUILD_*`` metadata is populated only in frozen / release builds.
A dev install / source checkout sees empty strings, and
:func:`version_string` simply returns ``__version__``.
"""
from __future__ import annotations

#: Package semantic version. **THIS IS THE ONLY LINE THE BUMP SCRIPT
#: REWRITES** — keep the format ``__version__ = "X.Y.Z"`` literal so
#: the regex in ``tools/bump_version.py`` matches.
__version__ = "0.4.1"


# Build-time metadata — populated by ``tools/build_exe.ps1`` (and the
# release CI workflow) into a sibling ``_build_info.py`` module which
# is gitignored. The fallback below means dev / source runs see empty
# strings without failing.
try:
    from ._build_info import (  # type: ignore[import-not-found]
        BUILD_COMMIT,
        BUILD_DATE,
    )
except ImportError:  # pragma: no cover — exercised in source / dev runs
    BUILD_COMMIT = ""
    BUILD_DATE = ""


def version_string() -> str:
    """Return a human-readable version including build metadata if known.

    Examples::

        '0.1.0'                       # dev / source build
        '0.1.0+ab12cd3'               # release build with embedded SHA
        '0.1.0+ab12cd3 (2026-05-07)'  # full build metadata
    """
    out = __version__
    if BUILD_COMMIT:
        out = f"{out}+{BUILD_COMMIT}"
    if BUILD_DATE:
        out = f"{out} ({BUILD_DATE})"
    return out


__all__ = ["__version__", "BUILD_COMMIT", "BUILD_DATE", "version_string"]
