# configure_test_display.py — Hosted Windows GUI desktop

Last updated: 2026-09-15

## Purpose
Give CI and release GUI tests an explicit, verified 1920x1080 desktop instead
of depending on the runner image's 1024x768 default. TradingLab's main window
already requires at least 1200px; actual widget width/scroll checks remain intact.
This does not claim support for smaller application desktops or loosen tests.

## Behavior
The stdlib-only Win32 wrapper reads the primary mode, tests a requested mode,
then applies it for the current session without registry persistence or reboot.
It requires a successful return code and verifies both Windows and Tk screen
dimensions before any tests start. Unsupported modes and insufficient resulting
dimensions fail setup explicitly. `--check-only` never changes display settings;
mutating invocation is restricted to `GITHUB_ACTIONS=true`.

When setup succeeds in Actions, `TRADINGLAB_CI_DISPLAY` records the actual Tk
dimensions in `GITHUB_ENV`. The existing coverage producer includes this variable
in its scope fingerprint; a different desktop is not a comparable baseline.
No application data directories, credentials, font/DPI settings or test
exclusions are modified.

## Wiring and tests
Used before tests in CI's Windows unit, mixed coverage, GUI coverage and smoke
jobs, and in both native Windows release build legs. The changed-line consumer
does not execute GUI tests and needs no display setup. Linux uses Xvfb explicitly.
`tests/unit/test_ci_display.py` pins the Win32 ABI, failure/no-op behavior, local
read-only safety and workflow placement. Hosted execution remains the acceptance
proof that the runner supports the requested mode.
