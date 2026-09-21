"""Tests for scripts/release_checksums.py.

Round-trip: generate a manifest over fake artifacts, verify it clean,
then confirm tampering / deletion is detected with a non-zero exit.
The script is stdlib-only and imported by file path (scripts/ is not
a package).
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


def _load_script() -> object:
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "release_checksums.py"
    assert script.is_file(), f"missing script: {script}"
    spec = importlib.util.spec_from_file_location("release_checksums", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def rc():
    return _load_script()


@pytest.fixture()
def artifacts(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "sub").mkdir(parents=True)
    (dist / "TradingLab-1.0.0-win64.zip").write_bytes(b"fake-zip-bytes-1")
    (dist / "TradingLab-1.0.0-arm64.zip").write_bytes(b"fake-zip-bytes-2")
    (dist / "sub" / "notes.txt").write_text("nested", encoding="utf-8")
    return dist


def test_generate_then_verify_clean(rc, artifacts: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "SHA256SUMS"
    assert rc.main(["--artifacts", str(artifacts), "--manifest", str(manifest)]) == 0

    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    # sha256sum-compatible "<hex>  <relpath>" lines, sorted by path.
    rels = [ln.split("  ", 1)[1] for ln in lines]
    assert rels == sorted(rels)
    assert rels == [
        "TradingLab-1.0.0-arm64.zip",
        "TradingLab-1.0.0-win64.zip",
        "sub/notes.txt",
    ]
    # Digest matches an independent hashlib computation.
    expected = hashlib.sha256(b"fake-zip-bytes-1").hexdigest()
    assert lines[1].startswith(expected + "  ")

    assert rc.main(["--verify", "--artifacts", str(artifacts), "--manifest", str(manifest)]) == 0


def test_verify_detects_tamper_and_missing(rc, artifacts: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "SHA256SUMS"
    assert rc.main(["--artifacts", str(artifacts), "--manifest", str(manifest)]) == 0

    with (artifacts / "TradingLab-1.0.0-win64.zip").open("ab") as fh:
        fh.write(b"tampered")
    (artifacts / "sub" / "notes.txt").unlink()

    assert rc.main(["--verify", "--artifacts", str(artifacts), "--manifest", str(manifest)]) == 1


def test_generate_excludes_manifest_itself(rc, artifacts: Path) -> None:
    manifest = artifacts / "SHA256SUMS"
    assert rc.main(["--artifacts", str(artifacts), "--manifest", str(manifest)]) == 0
    # Regenerating must not hash the manifest from the previous run.
    assert rc.main(["--artifacts", str(artifacts), "--manifest", str(manifest)]) == 0
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert all("SHA256SUMS" not in ln for ln in lines)


def test_verify_missing_manifest_fails(rc, tmp_path: Path) -> None:
    assert (
        rc.main(
            [
                "--verify",
                "--artifacts",
                str(tmp_path),
                "--manifest",
                str(tmp_path / "nope.txt"),
            ]
        )
        == 1
    )


def test_verify_rejects_malformed_and_traversal_lines(rc, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "good.zip").write_bytes(b"ok")
    digest = hashlib.sha256(b"ok").hexdigest()
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        f"{digest}  good.zip\n"
        "this is not a manifest line\n"
        f"{digest}  ../escape.zip\n",
        encoding="utf-8",
    )
    assert rc.main(["--verify", "--artifacts", str(dist), "--manifest", str(manifest)]) == 1
