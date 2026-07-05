# `splash.py` — startup splash controller (Feature B)

## Purpose

Bridges the PyInstaller bootloader's optional `pyi_splash` overlay
to application code:

1. User sees something within ~100 ms of `TradingLab.exe` launch
   instead of staring at an empty cursor for the 3-8 s startup.
2. Stage labels update as init progresses.
3. Splash closes once first paint is queued
   (`after_idle(splash.close)` in `app.py`).
4. Dev mode and tests get a no-op backend so the same code path
   works everywhere — no `if frozen` guards at call sites.

## Public API

```python
class SplashController(Protocol):
    def report(self, label: str) -> None: ...
    def close(self) -> None: ...

class NullSplashController: ...       # No-op. Default for dev/tests.
class PyiSplashController: ...        # Wraps pyi_splash. Frozen only.

def make_splash(*, force_disable: bool = False) -> SplashController: ...
def pyi_splash_available() -> bool: ...

STAGE_SETTINGS    = "Loading settings…"
STAGE_BUILDING_UI = "Building user interface…"
STAGE_FETCHING    = "Fetching ticker data…"
STAGE_READY       = "Ready."

ENV_DISABLE = "TRADINGLAB_NO_SPLASH"
CLI_DISABLE = "--no-splash"
```

## Selection rules (`make_splash`)

In order, first hit wins:

1. `force_disable=True` (kwarg) → `NullSplashController`.
2. `TRADINGLAB_NO_SPLASH=1` → null.
3. `--no-splash` in `sys.argv` → null.
4. `splash_enabled = False` in `defaults.py` / `settings.json`
   → null. (User-facing toggle in Settings; env / CLI
   short-circuit BEFORE this so frozen-build verify keeps a
   single off-switch.)
5. `pyi_splash` not importable (dev mode / no `Splash(...)` block
   in `.spec`) → null.
6. Otherwise → `PyiSplashController`.

Never raises. Broken `PyiSplashController()` construction falls
back to null. Broken settings read falls back to "splash on" —
end users never get a permanent black screen on corrupt settings.

## Invariants

- `NullSplashController.report` / `.close` never call into any
  external module — safe before Tk, matplotlib, logging.
- `PyiSplashController.report` wrapped in try/except. Splash is
  decorative; never a hard dependency. Failures swallowed.
- `.close()` is idempotent.
- Stage labels (`STAGE_*` constants) are canonical strings;
  hand-typing forbidden (unit tests grep `app.py` for `STAGE_*`).

## Wiring (in `app.py`)

```python
# In app.main(), BEFORE constructing ChartApp:
from .gui.splash import make_splash
splash = make_splash()
splash.report(STAGE_SETTINGS)
app = ChartApp(splash=splash)
# … app.mainloop() …
```

```python
# In ChartApp.__init__:
def __init__(self, *, splash: Optional[SplashController] = None):
    self._splash = splash or NullSplashController()
    self._splash.report(STAGE_BUILDING_UI)
    # … _build_ui() …
    self._splash.report(STAGE_FETCHING)
    self._load_data()
    self._splash.report(STAGE_READY)
    self.after_idle(self._splash.close)
```

## Error path

`app.main()` catches any startup exception and explicitly closes
the splash *before* the crash dialog appears — otherwise the
splash sits on top and the user can't see what failed.
