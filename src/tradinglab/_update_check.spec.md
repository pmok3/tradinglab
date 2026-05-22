# `_update_check.py` — background update check (Feature B)

## Purpose

Notify users non-disruptively when a newer TradingLab is available. The check:

- Runs on a daemon background thread (slow / dropped HTTP can't delay the GUI).
- Hard timeout (default 1.5 s).
- Silently fails on every error path (network, JSON, schema) — an update check
  that misses is invisible.
- Off by default: URL comes from env `TRADINGLAB_UPDATE_URL` or a `url=` kwarg.
  With neither, no thread is started.
- On a hit, invokes a user-supplied callback with the advertised version string.
  Callback runs on the worker thread; caller marshals to Tk via `self.after`.

## Public API

```python
ENV_URL           = "TRADINGLAB_UPDATE_URL"
DEFAULT_TIMEOUT_S = 1.5

def start_update_check(
    callback: Callable[[str], None],
    *,
    url: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    current_version: Optional[str] = None,
) -> bool: ...

def compare_versions(current: str, advertised: str) -> Optional[str]: ...
def _check_once(current_version, url, timeout) -> Optional[str]: ...
def _fetch_release_info(url, timeout) -> Optional[dict]: ...
def _extract_version_from_payload(payload) -> Optional[str]: ...
def _normalise_version(s) -> Optional[tuple]: ...
def _resolve_url(explicit) -> Optional[str]: ...
```

## Recognised response shapes

- Plain: `{"version": "0.2.3"}`
- GitHub Releases API: `{"tag_name": "v0.2.3", "html_url": "..."}` — directly
  compatible with `https://api.github.com/repos/<owner>/<repo>/releases/latest`.

## Version comparison (`compare_versions`)

`_normalise_version` strips leading `v`/`V`, drops pre-release / metadata
suffixes after `-` or `+`, pads missing fields with `0` (`"0.2"` → `(0,2,0)`),
returns `None` on non-numeric input. `compare_versions` returns the
**normalised** `MAJOR.MINOR.PATCH` string only when advertised > current;
returns `None` for equal, older, or malformed.

## Invariants

- `start_update_check` returns `False` (no thread started) when no URL is
  configured OR `current_version` resolves empty (defaults from
  `_version.__version__`).
- Worker thread is `daemon=True`, named `"TradingLab-UpdateCheck"`.
- `_fetch_release_info` swallows every exception (timeout, socket reset,
  DNS, cert failure, non-2xx, non-JSON) → `None` → "no update".
- Callback exception path is also swallowed.

## Security (audit H3 / L2)

- **URL scheme allow-list.** `_fetch_release_info` parses the URL
  via `urllib.parse.urlparse` and short-circuits to `None` unless
  the scheme is exactly `http` or `https`. Defends against `file://`,
  `gopher://`, `ftp://`, and other stdlib `urlopen` schemes that
  would be content-exclusion-policy-bypass surface if an attacker
  could control the env var or a `url=` kwarg.
- **Capped response read.** `resp.read(64 * 1024)` bounds the
  response at 64 KB. The real GitHub Releases payload is ~3 KB; a
  malicious or compromised server cannot OOM the chart by streaming
  unbounded JSON.
- **Daemon thread, no main-thread coupling.** Worker is fully
  detached — a stuck or compromised endpoint cannot block the GUI
  or block exit. Hard `timeout=1.5s` on the underlying `urlopen`.
- Tests in `tests/unit/test_update_check_hardening.py` lock the
  scheme allow-list and the read cap.

## Wiring (in `app.py`)

```python
# In ChartApp.__init__, AFTER _load_data:
from ._update_check import start_update_check

def _on_update_available(self, new_version: str) -> None:
    # Marshal to the Tk main thread.
    self.after(0, lambda: self._show_update_banner(new_version))

start_update_check(self._on_update_available)
```

`_show_update_banner` is a ttk.Frame mirroring `FirstRunBannerMixin`: one-line
text "Update vX.Y.Z available · Help → Check for Updates" + close button.
