# Release checksums

`scripts/release_checksums.py` (stdlib only) generates and verifies a
SHA-256 manifest for the release bundles, so anyone downloading a
`TradingLab-*.zip` can confirm it is byte-identical to what the build
produced.

## Generate

After building (see `docs/BUILDING_EXE.md`), with the zips under `dist/`:

```powershell
python scripts/release_checksums.py --artifacts dist --manifest dist/SHA256SUMS
```

This hashes every regular file under `dist/` (1 MiB chunked reads) and
writes `dist/SHA256SUMS` in `sha256sum`-compatible format
(`<hex>  <relpath>`, one line per file, sorted). The manifest file
itself is never hashed. Publish `SHA256SUMS` alongside the zips on the
GitHub release.

## Verify

Before uploading — and any time after downloading — re-check:

```powershell
python scripts/release_checksums.py --verify --artifacts dist --manifest dist/SHA256SUMS
```

Each entry reports `OK`, `FAILED` (content changed), or `MISSING`
(file absent). The exit status is `0` only when every listed file
verifies. Malformed manifest lines and `..` / absolute-path entries
are rejected rather than followed.

The manifest is also readable by GNU `sha256sum -c SHA256SUMS` from
inside the artifacts directory.
