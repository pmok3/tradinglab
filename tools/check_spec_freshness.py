"""Enforce TradingLab's colocated-spec freshness contract.

The check is Git-range-aware rather than filesystem-mtime-aware: CI checkouts
do not preserve useful modification times. Every production Python module
changed between ``base`` and ``head`` must have its sibling ``.spec.md`` in the
same diff, and every colocated spec must carry the canonical date header.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Sequence
from datetime import date
from pathlib import Path

_SOURCE_ROOT = Path("src") / "tradinglab"
_LAST_UPDATED_RE = re.compile(r"^Last updated: (\d{4}-\d{2}-\d{2})$")


def _spec_for_module(module: Path) -> Path:
    return module.with_suffix(".spec.md")


def _relative_paths_changed(
    repo_root: Path,
    *,
    base: str,
    head: str,
) -> set[Path]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "diff",
            "--name-only",
            "-z",
            f"{base}..{head}",
            "--",
            _SOURCE_ROOT.as_posix(),
        ],
        check=True,
        capture_output=True,
    )
    return {
        Path(raw.decode("utf-8"))
        for raw in result.stdout.split(b"\0")
        if raw
    }


def _header_error(spec: Path, repo_root: Path) -> str | None:
    rel = spec.relative_to(repo_root).as_posix()
    lines = spec.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3 or not lines[0].startswith("# "):
        return f"{rel}: expected an H1 title followed by a Last updated header"
    if lines[1] != "":
        return f"{rel}: expected a blank line after the H1 title"
    match = _LAST_UPDATED_RE.fullmatch(lines[2])
    if match is None:
        return f"{rel}: line 3 must be `Last updated: YYYY-MM-DD`"
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        return f"{rel}: Last updated value is not a valid ISO date"
    return None


def spec_header_errors(repo_root: Path) -> list[str]:
    """Return invalid or missing date-header errors for all colocated specs."""
    repo_root = repo_root.resolve()
    source_root = repo_root / _SOURCE_ROOT
    errors: list[str] = []
    for spec in sorted(source_root.rglob("*.spec.md")):
        error = _header_error(spec, repo_root)
        if error is not None:
            errors.append(error)
    return errors


def check_spec_freshness(
    repo_root: Path,
    *,
    base: str,
    head: str = "HEAD",
) -> list[str]:
    """Return contract violations for ``base..head``."""
    repo_root = repo_root.resolve()
    source_root = repo_root / _SOURCE_ROOT
    errors: list[str] = []

    modules = sorted(source_root.rglob("*.py"))
    for module in modules:
        spec = _spec_for_module(module)
        rel_module = module.relative_to(repo_root).as_posix()
        if not spec.exists():
            errors.append(f"{rel_module}: missing colocated {_spec_for_module(Path(rel_module))}")

    errors.extend(spec_header_errors(repo_root))

    changed = _relative_paths_changed(repo_root, base=base, head=head)
    changed_modules = sorted(
        path
        for path in changed
        if path.suffix == ".py"
        and path.parts[:2] == _SOURCE_ROOT.parts
        and (repo_root / path).exists()
    )
    for module in changed_modules:
        spec = _spec_for_module(module)
        if spec not in changed:
            errors.append(
                f"{module.as_posix()}: changed without colocated "
                f"{spec.as_posix()} in {base}..{head}"
            )

    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Git base revision")
    parser.add_argument("--head", default="HEAD", help="Git head revision")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to this script's parent repository)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    errors = check_spec_freshness(
        args.repo_root,
        base=args.base,
        head=args.head,
    )
    if errors:
        print("Spec freshness check failed:")
        for error in errors:
            print(f"  - {error}")
        print(
            "\nUpdate each affected spec's behavior contract, or acknowledge a "
            "non-behavioral module change by updating its Last updated date."
        )
        return 1

    print(f"Spec freshness check passed for {args.base}..{args.head}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
