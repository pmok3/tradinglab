# Release checksums

`scripts/release_checksums.py` (stdlib only) generates and verifies a
SHA-256 manifest for the release bundles. A downloaded `TradingLab-*.zip`
can be checked against the published digest to detect corruption or changes.
The manifest is **unsigned**: it does not authenticate the publisher or
protect against replacement of both the manifest and artifacts.

## Generate

Normal releases are built by the tag-triggered `release.yml`, not locally.
After collecting both architecture zips, the publish job requires exactly
one `win64` and one `winarm64` zip, generates the manifest, verifies it,
and publishes `SHA256SUMS` alongside the zips. A failure stops publication.
Manual build-only runs (`publish_release=false`) do not run the publish job.

For local debugging only, place artifacts in a dedicated `dist/` directory:

```powershell
python scripts\release_checksums.py --artifacts dist --manifest dist\SHA256SUMS
```

This hashes every regular file under `dist/` (1 MiB chunked reads) and
writes `dist/SHA256SUMS` in `sha256sum`-compatible format
(`<hex>  <relpath>`, one line per file, sorted). The manifest file
itself is never hashed. An empty artifact directory fails generation.
Paths resolving outside the artifact directory (including through symlinks)
are rejected; contained symlink files are not included by generation.

## Verify

Before uploading — and any time after downloading — re-check:

```powershell
python scripts\release_checksums.py --verify --artifacts dist --manifest dist\SHA256SUMS
```

Each entry reports `OK`, `FAILED` (content changed), or `MISSING`
(file absent). The exit status is `0` only when every listed file
verifies and at least one file was checked. Empty/comment-only manifests
fail. Malformed lines, POSIX/Windows absolute paths, drive-relative or
rooted paths, backslashes, alternate streams, traversal, and resolved
symlink escapes are rejected rather than followed. Verification checks
listed files, not extra unlisted files. Download both architecture zips
to verify the complete published manifest.

The manifest is also readable by GNU `sha256sum -c SHA256SUMS` from
inside the artifacts directory.
