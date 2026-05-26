# `_version.py`

## Purpose
Single source of truth for the package's PEP-440 semantic version. Five external systems read this file; keep the format `__version__ = "X.Y.Z"` literal so the bump-script regex matches.

## Public surface
- `__version__: str` — `"MAJOR.MINOR.PATCH"`. **THIS IS THE ONLY LINE `tools/bump_version.py` REWRITES.**
- `BUILD_COMMIT: str` — git short SHA. Empty string in dev / source builds. Populated by `tools/build_exe.ps1` (and the release CI workflow) into a sibling `_build_info.py` that is gitignored.
- `BUILD_DATE: str` — ISO-style build date. Same provenance rules.
- `version_string() -> str` — human-readable: `"0.3.0"`, `"0.3.0+abc1234"`, or `"0.3.0+abc1234 (2026-05-26)"` depending on which build metadata is present.

## Consumers
| Consumer | What it reads |
|---|---|
| `tradinglab.__init__` | `__version__`, `version_string` re-exported as public API |
| `pyproject.toml` `[tool.setuptools.dynamic]` | `__version__` — wheel / sdist / `pip install -e .` all see the same number |
| `tools/bump_version.py` | rewrites the `__version__` line |
| `tools/build_exe.ps1` | reads the version, drops the `_build_info.py` sibling with git SHA + date |
| `--version` CLI / About dialog | `version_string()` |

## Format invariants
- `__version__` MUST match `^\d+\.\d+\.\d+$`. Pre-release / dev suffixes are not currently supported by the bump script.
- `_build_info.py` is gitignored. Its absence in dev / source runs is normal and produces a friendly empty-string fallback.

## Tests
`tests/unit/test_versioning.py` exercises `__main__ --version` and `--help` via subprocess.
