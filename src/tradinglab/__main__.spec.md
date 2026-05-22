# __main__.py — Spec

## Purpose
Enables `python -m tradinglab` to launch the GUI. Delegates to `app.main()` after acquiring the Windows single-instance mutex.

## Public API
- No public names exported; guards `main()` with `if __name__ == "__main__":` so importing the module doesn't start the event loop.

## Dependencies
- Internal: `.app.main`, `._single_instance.single_instance_guard`, `._single_instance.release_single_instance`.
- External: none.

## Design Decisions
- Separate file (rather than `if __name__ == "__main__"` inside `app.py`) so `python -m tradinglab` works without reading the big `app.py` dispatch block.
- **Feature B — single-instance protection:** Before constructing the splash or calling `main()`, the module calls `single_instance_guard()` which tries to acquire a single-instance guard. On **Windows** that's a named kernel mutex (`Local\TradingLab.SingleInstance`); on a double-launch the second process resolves the already-running TradingLab window by `EnumWindows` + title-prefix match (not `FindWindowW("Tk", …)` — every Python+Tk app shares the `"Tk"` class), brings it forward via `SetForegroundWindow`, and `sys.exit(0)`s. On **POSIX (Linux / macOS)** the guard is an exclusive `fcntl.flock` on a lockfile under `app_data_dir()`; the second instance prints a one-line "TradingLab is already running" hint to stderr (no cross-toolkit focus primitive ships with the app) and exits 0. If `fcntl` is unavailable the POSIX path degrades to `(True, None)` so the user always launches *something*. The handle (mutex int on Windows, file object on POSIX) is released in a `finally` block so a `Ctrl+C` / crash doesn't permanently block subsequent launches.

## Invariants
- Importing `tradinglab.__main__` must not run `main()` — only executing it as `__main__` does.
- The mutex handle is released exactly once even when `main()` raises.

## Testing
- Not exercised directly by smoke checks; covered implicitly by `check_00_import`.
- `tests/unit/test_single_instance.py` exercises the guard with injected fake handlers so the Win32 calls don't need to run.

## Known limitations / Future work
- The "bring existing window forward" path uses `SetForegroundWindow`, which Windows can refuse if the calling process isn't the foreground window. In practice the second-launch flash + status-bar toast on the existing window is enough; future work could use `AllowSetForegroundWindow` + a tiny IPC ping to make this 100% reliable.

