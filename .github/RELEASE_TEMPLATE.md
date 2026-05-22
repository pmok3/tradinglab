# Release notes template

Paste this near the top of every GitHub Release body. The smart-screen note is the most-asked end-user question — keeping it triplicated (here, in the README, and in the in-app first-run banner) means no one has to hunt for it.

---

## For end users (no developer setup required)

1. Download `TradingLab-<version>-win64.zip` below.
2. Extract anywhere (e.g. `Documents\TradingLab\`).
3. Double-click `TradingLab.exe`.

> **First-launch SmartScreen warning** — Windows may show
> "Windows protected your PC" because this build is **not
> code-signed**. This is normal for independent software with no
> purchase history; click **More info → Run anyway** to launch.
> Subsequent launches typically don't warn.

App data (settings, watchlists, cached candles, encrypted
credentials, logs) lives at `%LOCALAPPDATA%\TradingLab\`. Use
**Help → Reveal Data Folder** to open it; **Help → Reset & Quit**
purges everything and starts fresh.

## What's new in this release

<!-- Auto-generated below by softprops/action-gh-release.
     Edit before publishing if you want a curated summary. -->

## Verification

Every release is built by the [Release workflow](../../actions/workflows/release.yml)
on `windows-latest`. The workflow runs the full unit + smoke + scanner
test suite BEFORE PyInstaller (`tests/unit tests/smoke tests/scanner`,
516+ tests) and then `tools/verify_frozen.ps1` against the resulting
exe (launches the GUI, waits for the main window, posts WM_CLOSE,
asserts a clean exit). A failing test or smoke probe blocks the release
upload.

## Reporting bugs

If the app crashes, a diagnostic file is written to
`%LOCALAPPDATA%\TradingLab\logs\crash-<timestamp>.txt`. Attach it
to your bug report.
