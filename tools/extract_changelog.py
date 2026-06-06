#!/usr/bin/env python3
"""Extract a single version's section from ``CHANGELOG.md``.

Used to feed curated release notes to the GitHub Release (the
``.github/workflows/release.yml`` publish job pipes this into
``softprops/action-gh-release``'s ``body_path`` so the changelog is
visible in the release notes), and to backfill notes for existing
releases.

The changelog follows *Keep a Changelog*-style version headers:

    ## [0.3.5] - 2026-06-06
    ### Added
    - ...
    ## [0.3.4] - 2026-06-05
    ...

``extract_section("0.3.5", text)`` returns everything BETWEEN the
matching ``## [0.3.5] ...`` header and the next ``## `` header
(exclusive), stripped — i.e. the ``### Added/Changed/Fixed`` body. The
version header line itself is omitted because the GitHub Release title
already shows the version.

Usage::

    python tools/extract_changelog.py 0.3.5                      # -> stdout
    python tools/extract_changelog.py v0.3.5 CHANGELOG.md out.md # -> file (UTF-8)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_VERSION_HEADER = re.compile(r"^##\s+\[([^\]]+)\]")


def extract_section(version: str, changelog: str) -> str | None:
    """Return the changelog body for ``version`` or ``None`` if absent.

    ``version`` may carry a leading ``v`` (``v0.3.5``); it is stripped.
    Matching is exact on the bracketed version token.
    """
    wanted = version.lstrip("vV").strip()
    out: list[str] = []
    capturing = False
    for line in changelog.splitlines():
        header = _VERSION_HEADER.match(line)
        if header is not None:
            if capturing:
                break  # reached the next version section
            if header.group(1).strip() == wanted:
                capturing = True
            continue  # never emit a version-header line
        if capturing:
            out.append(line)
    if not capturing:
        return None
    return "\n".join(out).strip()


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(
            "usage: extract_changelog.py <version> [changelog] [out_file]\n")
        return 2
    version = argv[0]
    path = Path(argv[1]) if len(argv) > 1 else Path("CHANGELOG.md")
    out_path = Path(argv[2]) if len(argv) > 2 else None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"cannot read {path}: {exc}\n")
        return 2
    section = extract_section(version, text)
    if not section:
        sys.stderr.write(
            f"no CHANGELOG section found for version {version!r} in {path}\n")
        return 1
    body = section + "\n"
    if out_path is not None:
        # Always UTF-8 — the changelog carries → / — / … glyphs that the
        # Windows console default (cp1252) can't encode via stdout.
        out_path.write_text(body, encoding="utf-8")
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - older/odd stdout objects
            pass
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
