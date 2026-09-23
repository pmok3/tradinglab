"""Tests for scripts/release_checksums.py.

Round-trip: generate a manifest over fake artifacts, verify it clean,
then confirm tampering / deletion is detected with a non-zero exit.
The script is stdlib-only and imported by file path (scripts/ is not
a package).
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import zipfile
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


def test_iter_artifact_files_sorts_by_posix_string(rc, tmp_path: Path) -> None:
    # Manifest order must be deterministic across platforms. Sorting
    # ``Path`` objects directly is case-insensitive on Windows
    # (``PurePath`` uses ``normcase``), which disagrees with a plain
    # string sort of the manifest lines — this failed
    # ``test_generate_then_verify_clean`` on Windows CI.
    dist = tmp_path / "dist"
    (dist / "sub").mkdir(parents=True)
    (dist / "sub" / "notes.txt").write_text("nested", encoding="utf-8")
    (dist / "Zebra.zip").write_bytes(b"z")
    (dist / "apple.zip").write_bytes(b"a")
    got = [p.as_posix() for p in rc.iter_artifact_files(dist)]
    assert got == sorted(got)
    assert got == ["Zebra.zip", "apple.zip", "sub/notes.txt"]


@pytest.mark.parametrize("rel", [
    "C:/outside.zip", "C:\\outside.zip", "C:outside.zip", "\\outside.zip",
    "//server/share/outside.zip", "\\\\server\\share\\outside.zip",
    "/outside.zip", "../outside.zip", "sub/../../outside.zip",
    "sub\\..\\..\\outside.zip", "asset.zip:stream",
])
def test_manifest_rejects_nonportable_or_escaping_paths(rc, tmp_path, monkeypatch, rel):
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{'a' * 64}  {rel}\n", encoding="utf-8")
    monkeypatch.setattr(rc, "sha256_file", lambda _: pytest.fail("unsafe path must not be opened"))
    assert rc.parse_manifest_line(manifest.read_text()) is None
    assert rc.verify(tmp_path, manifest) == 1


@pytest.mark.parametrize("content", ["", "\n", "# no artifacts\n", "malformed\n"])
def test_manifest_with_no_checks_fails(rc, tmp_path, content):
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(content, encoding="utf-8")
    assert rc.verify(tmp_path, manifest) == 1


def test_generation_without_artifacts_fails(rc, tmp_path):
    manifest = tmp_path / "SHA256SUMS"
    assert rc.generate(tmp_path, manifest) == 1
    assert not manifest.exists()


def test_symlink_escape_fails_before_hashing(rc, tmp_path, monkeypatch):
    root = tmp_path / "dist"
    root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside = outside_dir / "outside.zip"
    outside.write_bytes(b"outside")
    link = root / "linked"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        if sys.platform != "win32":
            pytest.skip(f"symlink creation unavailable: {exc}")
        # Windows junctions exercise real reparse-point containment without
        # requiring the symlink privilege or changing developer-mode settings.
        created = subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(outside_dir)],
            capture_output=True, text=True, timeout=10,
        )
        assert created.returncode == 0, created.stderr
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{hashlib.sha256(b'outside').hexdigest()}  linked/outside.zip\n", encoding="utf-8")
    try:
        monkeypatch.setattr(rc, "sha256_file", lambda _: pytest.fail("outside artifact must not be hashed"))
        assert rc.verify(root, manifest) == 1
        assert rc.main(["--artifacts", str(root), "--manifest", str(manifest)]) == 1
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()
        assert outside.read_bytes() == b"outside"


def test_resolved_parent_escape_fails_before_hashing(rc, tmp_path, monkeypatch):
    """Pin containment even where the OS disallows unprivileged symlink creation."""
    root = tmp_path / "dist"
    root.mkdir()
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{'a' * 64}  linked/asset.zip\n", encoding="utf-8")
    resolve = Path.resolve
    target = root / "linked" / "asset.zip"
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw:
                        tmp_path / "outside.zip" if self == target else resolve(self, *a, **kw))
    monkeypatch.setattr(rc, "sha256_file", lambda _: pytest.fail("outside artifact must not be hashed"))
    assert rc.verify(root, manifest) == 1


def test_cli_round_trip_real_zip_assets_and_unreadable_manifest(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    for arch in ("win64", "winarm64"):
        with zipfile.ZipFile(root / f"TradingLab-1.0.0-{arch}.zip", "w") as archive:
            archive.writestr("TradingLab/README.txt", f"test bundle {arch}")
    script = Path(__file__).resolve().parents[2] / "scripts" / "release_checksums.py"
    manifest = root / "SHA256SUMS"
    command = [sys.executable, str(script), "--artifacts", str(root), "--manifest", str(manifest)]
    generated = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert generated.returncode == 0, generated.stderr
    verified = subprocess.run([*command, "--verify"], capture_output=True, text=True, timeout=10)
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout.count(": OK") == 2
    for line in manifest.read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert digest == hashlib.sha256((root / name).read_bytes()).hexdigest()
    manifest.write_bytes(b"\xff")
    unreadable = subprocess.run([*command, "--verify"], capture_output=True, text=True, timeout=10)
    assert unreadable.returncode == 1
    assert "checksum operation failed" in unreadable.stderr
