"""Generate and verify a SHA-256 manifest for TradingLab release artifacts.

Stdlib only. Two modes:

Generate (default):
    python scripts/release_checksums.py [--artifacts DIR] [--manifest PATH]

    Walks DIR (default ``dist/``), hashes every regular file, and writes a
    manifest in ``sha256sum``-compatible format (``<hex>  <relpath>``),
    one line per file, sorted by relative path. The manifest file itself
    is never hashed, even when it lives inside DIR.

Verify:
    python scripts/release_checksums.py --verify [--manifest PATH] [--artifacts DIR]

    Re-hashes every file listed in the manifest (resolved relative to
    DIR) and reports ``OK`` / ``FAILED`` / ``MISSING`` per entry. Exit
    status 0 only when every listed file verifies.

Intended use: after ``tools/build_exe.ps1`` (or the ``Release``
workflow) produces the ``TradingLab-<version>-win64.zip`` /
``TradingLab-<version>-arm64.zip`` bundles under ``dist/``, generate
the manifest, publish it alongside the zips, and verify before upload.
See ``docs/RELEASE_CHECKSUMS.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 MiB reads keep hashing off the hot loop


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of ``path`` (chunked reads)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_artifact_files(root: Path, *, exclude: Path | None = None) -> list[Path]:
    """Sorted list of regular files under ``root`` (relative paths)."""
    files: list[Path] = []
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        rel = candidate.relative_to(root)
        if exclude is not None and candidate.resolve() == exclude.resolve():
            continue
        files.append(rel)
    return files


def format_manifest_line(digest: str, rel: Path) -> str:
    """``sha256sum``-compatible line: ``<hex>  <relpath>``."""
    return f"{digest}  {rel.as_posix()}"


def parse_manifest_line(line: str) -> tuple[str, str] | None:
    """Parse one manifest line into ``(digest, relpath)``; ``None`` if malformed."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    # Accept both "  " (text mode) and " *" (binary mode) separators.
    for sep in ("  ", " *"):
        if sep in stripped:
            digest, _, rel = stripped.partition(sep)
            break
    else:
        return None
    digest = digest.strip()
    rel = rel.strip()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
        return None
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    return digest, rel


def generate(root: Path, manifest: Path) -> int:
    """Hash every artifact under ``root`` and write the manifest. Returns 0."""
    root = root.resolve()
    manifest = manifest.resolve()
    if not root.is_dir():
        print(f"artifacts directory not found: {root}", file=sys.stderr)
        return 1
    lines = [
        format_manifest_line(sha256_file(root / rel), rel)
        for rel in iter_artifact_files(root, exclude=manifest)
    ]
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(line)
    print(f"wrote {len(lines)} entr{'y' if len(lines) == 1 else 'ies'} to {manifest}")
    return 0


def verify(root: Path, manifest: Path) -> int:
    """Re-hash every manifest entry; report OK/FAILED/MISSING. Returns 0/1."""
    root = root.resolve()
    if not manifest.is_file():
        print(f"manifest not found: {manifest}", file=sys.stderr)
        return 1
    failures = 0
    checked = 0
    for lineno, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        parsed = parse_manifest_line(raw)
        if parsed is None:
            if raw.strip() and not raw.strip().startswith("#"):
                print(f"line {lineno}: malformed, FAILED")
                failures += 1
            continue
        digest, rel = parsed
        target = root / rel
        checked += 1
        if not target.is_file():
            print(f"{rel}: MISSING")
            failures += 1
            continue
        if sha256_file(target) == digest.lower():
            print(f"{rel}: OK")
        else:
            print(f"{rel}: FAILED")
            failures += 1
    print(f"checked {checked} file(s), {failures} failure(s)")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Generate or verify a SHA-256 manifest for release artifacts."
    )
    ap.add_argument(
        "--artifacts",
        type=Path,
        default=Path("dist"),
        help="Directory holding the release artifacts (default: dist/).",
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=Path("dist") / "SHA256SUMS",
        help="Manifest file to write/read (default: dist/SHA256SUMS).",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Verify artifacts against the manifest instead of generating it.",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verify:
        return verify(args.artifacts, args.manifest)
    return generate(args.artifacts, args.manifest)


if __name__ == "__main__":
    raise SystemExit(main())
