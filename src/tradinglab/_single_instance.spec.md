# `_single_instance.py` — single-instance protection (Feature B)

## Purpose

Stop a second `TradingLab.exe` double-click (Windows) — or a second launch of the
frozen macOS / Linux bundles — from spawning a second GUI process that fights
over `drawings.json` / `settings.json` / `sandbox_last.json`.

## Mechanism

Two backends, picked by `sys.platform`:

### Windows backend (named kernel mutex)

Mutex name: `Local\TradingLab.SingleInstance` (the `Local\` prefix scopes it to
the current Windows user session, so different users — or a normal + elevated
session — each get their own instance).

- `CreateMutexW` sets `GetLastError() == ERROR_ALREADY_EXISTS` (183) when the
  named mutex pre-exists.
- Focus dance: `EnumWindows` → match title prefix → `IsIconic` →
  `ShowWindow(SW_RESTORE)` if minimized → `SetForegroundWindow`. Title-prefix
  match (not window class) because every Python+Tk app shares class `"Tk"`;
  `ChartApp.__init__` sets `self.title(f"TradingLab v{version}")`.

### POSIX backend (`fcntl.flock`)

Lockfile: `<app_data_dir()>/.tradinglab.singleinstance.lock`.

- First instance opens `"w"` and takes `flock(fd, LOCK_EX | LOCK_NB)`; the FD
  is held alive for process lifetime. OS releases the lock on FD close or
  process death, so stale on-disk lockfiles from a crashed previous instance
  are not a problem (flock is per-FD, not per-file).
- Second instance gets `EWOULDBLOCK` / `EAGAIN` → `("held", None)`.
- Lockfile records current PID (informational only).
- No cross-toolkit X11/Wayland/AppKit focus primitive ships, so the POSIX
  helper prints a stderr hint and trusts the dock-icon-click to raise the
  existing window.

Graceful degradation when `fcntl` is missing OR data dir is unwritable:
helper returns `("unsupported", None)` and `acquire_single_instance` returns
`(True, None)` — the user always gets *some* process running.

## Public API

```python
MUTEX_NAME            = "Local\\TradingLab.SingleInstance"
WINDOW_TITLE_PREFIX   = "TradingLab"
POSIX_LOCK_FILENAME   = ".tradinglab.singleinstance.lock"
ERROR_ALREADY_EXISTS  = 183

def acquire_single_instance(
    name=MUTEX_NAME, *, posix_lock_path: Optional[str] = None,
) -> tuple[bool, Optional[Any]]: ...
def release_single_instance(handle: Optional[Any]) -> None: ...
def focus_existing_window(title_prefix=WINDOW_TITLE_PREFIX) -> bool: ...
def single_instance_guard() -> tuple[bool, Optional[Any]]: ...
```

`handle` is `Optional[Any]`: Windows backend returns an int mutex handle, POSIX
backend returns the open file object. `release_single_instance` dispatches by
shape (file-like duck-types → POSIX path; everything else → Windows path).

The six platform primitives (`create_mutex`, `close_handle`, `find_window`,
`focus_window`, `acquire_posix_lock`, `release_posix_lock`) are exposed
through `_HANDLERS` so tests can monkey-patch them on any host.

POSIX handler contract — `acquire_posix_lock(path)` returns `(status, handle)`:

- `("acquired", file_obj)` — we own the lock.
- `("held", None)` — another live process holds it.
- `("unsupported", None)` — no `fcntl` / I/O error / etc.

## Selection rules

`_resolve_handler(key)`:

1. Registered handler in `_HANDLERS[key]` → use it (tests).
2. `sys.platform == "win32"` → real ctypes impl for Windows keys; `None` for
   POSIX keys.
3. Otherwise → real `fcntl` impl for POSIX keys; `None` for Windows keys.

`_is_windows_active()` returns `True` on Windows OR when a test has injected
a `create_mutex` handler — this lets the Windows test suite run on Linux/macOS
CI hosts.

## Invariants

- `acquire_single_instance` **never raises**; any internal failure degrades to
  `(True, None)`.
- `release_single_instance(None)` is a safe no-op. OS-level cleanup on process
  termination (Windows mutex; POSIX FD/flock) makes explicit release hygienic
  rather than mandatory.
- `release_single_instance` on a file-like handle WITHOUT a registered
  `release_posix_lock` handler still calls `.close()` so the FD doesn't leak
  (matters under test reset).
- `focus_existing_window` walks **all** top-level visible windows via
  `EnumWindows` and matches by title prefix (not window class).
- On POSIX `focus_existing_window` returns `False`; `single_instance_guard`
  falls back to the stderr hint.

## Wiring (in `__main__.py`)

```python
proceed, handle = single_instance_guard()
if not proceed:
    return 0
try:
    return tradinglab.app.main()
finally:
    release_single_instance(handle)
```

## Failure modes (graceful)

| Scenario                              | Behaviour                                              |
|---------------------------------------|--------------------------------------------------------|
| Windows: `CreateMutexW` raises        | `(True, None)`.                                        |
| Windows: second instance, focus denied | Second exits 0; existing window at least nudged.       |
| Windows: existing minimized           | `ShowWindow(SW_RESTORE)` before `SetForegroundWindow`. |
| POSIX: `fcntl` unavailable            | `("unsupported", None)` → `(True, None)`.              |
| POSIX: data dir unwritable            | `("unsupported", None)` → `(True, None)`.              |
| POSIX: another instance holds lock    | `("held", None)` → `(False, None)` + stderr hint.      |
| POSIX: stale lockfile (crashed app)   | OS released flock on FD-close → new instance acquires. |
