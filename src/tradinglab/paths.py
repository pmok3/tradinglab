"""Single source of truth for the user-data directory layout.

Every module that needs to read or write persistent state (settings,
watchlists, candle cache, event cache, logs, indicator plugins, broker
tokens) routes through :func:`app_data_dir` (or one of the
subdirectory helpers below) so the layout is defined in exactly one
place. This eliminates the drift we used to have where each subsystem
hand-rolled its own ``%LOCALAPPDATA%\\tradinglab\\…`` join (and one
— ``data.schwab_auth`` — silently used a different root entirely).

Layout (Windows; macOS / Linux mirrored under their conventional
roots)::

    %LOCALAPPDATA%\\TradingLab\\
    ├── settings.json              (optional, explicit save)
    ├── watchlists.json            (optional, explicit save)
    ├── credentials.dat            (DPAPI-encrypted broker secrets)
    ├── cache\\                     (disk_cache candle pickles)
    ├── logs\\                      (status-YYYY-MM-DD.log, crash dumps)
    ├── events\\                    (events.cache bundle pickles)
    ├── indicators\\                (user-authored .py plugins)
    └── tokens\\                    (Schwab OAuth refresh tokens)

Env-var overrides (in precedence order):

* ``TRADINGLAB_DATA_DIR`` — new, **takes precedence over every other
  override**. Redirects the entire tree. Use this in tests and in any
  containerised / sandboxed runtime.
* ``TRADINGLAB_CACHE_DIR`` — **legacy**, still honored for
  back-compat with the existing smoke harness; if set without
  ``TRADINGLAB_DATA_DIR``, it becomes the data root (everything
  flows in, not just the candle cache). New code should use
  ``TRADINGLAB_DATA_DIR``.
* ``TRADINGLAB_TOKEN_DIR`` — legacy, narrow-scope; only affects the
  Schwab token cache. Honored for back-compat with the dev harness.

Migration (one-shot, idempotent):

On first call within a process, :func:`app_data_dir` looks for legacy
roots and moves them to the canonical location:

* Windows: ``%LOCALAPPDATA%\\tradinglab\\`` (lowercase folder name
  from earlier builds) is left untouched because NTFS is
  case-insensitive — both the legacy and new paths resolve to the
  same directory.
* All platforms: ``~/.tradinglab/tokens/`` → ``<app_data>/tokens/``
  (this was the inconsistent location used by Schwab OAuth before the
  unification).

Migration failures are non-fatal: the new location is returned even if
the move couldn't complete (a stale file handle, permission denied,
etc.). The user-visible symptom in that rare case is having to re-auth
Schwab once; nothing catastrophic.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

#: Canonical user-data folder name. Branded form (CamelCase) for
#: Explorer / Reveal-in-Finder consistency with the app name.
_APP_FOLDER_NAME = "TradingLab"

#: One-shot migration flag — set after the first successful resolution
#: so subsequent calls skip the legacy-path probing.
_MIGRATION_DONE = False


def _platform_base_dir() -> Path:
    """Return the platform-conventional parent of our app-data folder.

    Windows: ``%LOCALAPPDATA%`` (or ``~/AppData/Local`` as fallback).
    macOS:   ``~/Library/Application Support``.
    Linux:   ``~/.local/share`` (XDG_DATA_HOME if set).
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base)
        return Path.home() / "AppData" / "Local"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def _resolve_root() -> Path:
    """Resolve the data root honoring env-var overrides, no migration.

    Internal helper; callers should use :func:`app_data_dir` which adds
    the one-shot migration on top.
    """
    explicit = os.environ.get("TRADINGLAB_DATA_DIR")
    if explicit:
        return Path(explicit)
    legacy_cache = os.environ.get("TRADINGLAB_CACHE_DIR")
    if legacy_cache:
        return Path(legacy_cache)
    return _platform_base_dir() / _APP_FOLDER_NAME


def _migrate_legacy_locations(target_root: Path) -> None:
    """One-shot migration from pre-unification paths.

    Idempotent and silent on failure: we never crash the user's launch
    over a migration glitch. The migration *itself* is documented in
    the module docstring; this function is the implementation.

    Symlink-safe: every legacy-path probe uses :func:`Path.is_symlink`
    to refuse following same-user-attacker-planted symlinks (e.g. a
    symlink at ``~/.tradinglab/tokens`` pointing at a sensitive
    directory whose contents would otherwise get moved into our
    canonical location).
    """
    # Migrate ``~/.tradinglab/tokens/`` → ``<root>/tokens/``.
    # The old Schwab-only cache location used ``~`` because it pre-dated
    # the LOCALAPPDATA convention.
    try:
        legacy_tokens = Path.home() / ".tradinglab" / "tokens"
        new_tokens = target_root / "tokens"
        if (legacy_tokens.is_dir()
                and not legacy_tokens.is_symlink()
                and not new_tokens.exists()):
            new_tokens.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_tokens), str(new_tokens))
            # Best-effort cleanup of the now-empty ``~/.tradinglab/``.
            try:
                legacy_tokens.parent.rmdir()
            except OSError:
                pass  # not empty (user had other state) — leave it
    except OSError:
        pass  # silent — see module docstring

    # Migrate ``<base>/tradinglab/`` → ``<base>/TradingLab/`` on
    # case-sensitive filesystems (macOS HFS+ case-insensitive default
    # and NTFS case-insensitive resolution mean Windows users see this
    # as a no-op).
    try:
        legacy = _platform_base_dir() / "tradinglab"
        if (legacy.is_dir()
                and not legacy.is_symlink()
                and not target_root.exists()
                and legacy.resolve() != target_root.resolve()):
            target_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(target_root))
    except OSError:
        pass

    # One-shot purge of legacy pickle caches. Prior versions wrote
    # candle and event caches as ``.pkl`` files; those files are
    # ``pickle.load``-able which is arbitrary-code-execution by
    # design. The new code (:mod:`tradinglab.disk_cache`,
    # :mod:`tradinglab.events.cache`) only reads / writes JSON, so
    # any leftover ``.pkl`` on disk is at best stale and at worst a
    # planted RCE payload waiting for the next chart open. Unlink
    # them all on first launch after the upgrade; the user pays one
    # re-fetch per symbol.
    try:
        _purge_legacy_pickle_caches(target_root)
    except OSError:
        pass


def _purge_legacy_pickle_caches(root: Path) -> int:
    """Delete every ``*.pkl`` file from the candle and events cache dirs.

    Returns the count of files actually removed. Idempotent: a
    second call returns 0. Symlinks are unlinked (not followed) so a
    planted ``link → /etc/shadow`` cannot trick us into deleting
    something outside the cache.

    Called from :func:`_migrate_legacy_locations`; not part of the
    public API but exported below for unit testing.
    """
    removed = 0
    candidate_dirs = (root, root / "events")
    for d in candidate_dirs:
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                # Use lstat-based checks so a symlink is identified
                # as such and unlinked without dereferencing.
                if not entry.is_file() and not entry.is_symlink():
                    continue
                if entry.suffix.lower() != ".pkl":
                    continue
                entry.unlink()
                removed += 1
            except OSError:
                continue
    return removed


# yfinance keeps a SQLite cache of ticker → timezone mappings at
# ``platformdirs.user_cache_dir("py-yfinance")/tkr-tz.db``. The file is
# tiny (~25 KB) and refills cheaply on demand (~1 HTTP round-trip per
# unique ticker). Concurrent access from multiple Python processes
# (e.g. the live app + a pytest run) corrupts the SQLite file, after
# which yfinance returns a misleading ``Ticker '...' not found`` for
# every uncached symbol. Wiping it on every launch trades ~5–10 cheap
# HTTP calls on day 1 of a fresh launch for full corruption immunity.
# ``cookies.db`` (the OAuth-like session cookie cache) is deliberately
# left in place — it's not part of this corruption class.
_YFINANCE_TIMEZONE_DB = "tkr-tz.db"
_YFINANCE_TIMEZONE_DB_SIDECARS = (
    "tkr-tz.db",
    "tkr-tz.db-journal",
    "tkr-tz.db-wal",
    "tkr-tz.db-shm",
)


def _yfinance_cache_dir() -> Optional[Path]:
    """Return the directory yfinance uses for its caches, or ``None``
    when ``platformdirs`` is unavailable.

    Mirrors yfinance's own ``platformdirs.user_cache_dir("py-yfinance")``
    call so we resolve to the same file the library reads. Returns
    ``None`` (not a raise) so the caller can no-op silently — the
    self-heal is best-effort.
    """
    try:
        from platformdirs import user_cache_dir
    except ImportError:
        return None
    try:
        return Path(user_cache_dir("py-yfinance"))
    except Exception:  # noqa: BLE001
        return None


def wipe_yfinance_timezone_cache() -> int:
    """Unlink yfinance's ``tkr-tz.db`` (and SQLite sidecar files) so the
    next yfinance call rebuilds a fresh, uncorrupted cache.

    Returns the number of files actually removed. Idempotent: a second
    call on the same launch returns 0. Symlinks are unlinked (not
    followed) — same security posture as
    :func:`_purge_legacy_pickle_caches`. Called once per process from
    :class:`tradinglab.app.ChartApp.__init__` BEFORE the first
    yfinance fetch is submitted.
    """
    cache_dir_path = _yfinance_cache_dir()
    if cache_dir_path is None:
        return 0
    removed = 0
    for name in _YFINANCE_TIMEZONE_DB_SIDECARS:
        target = cache_dir_path / name
        try:
            if not target.is_file() and not target.is_symlink():
                continue
            target.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def app_data_dir() -> Path:
    """Return the (created-if-missing) user-data root.

    First call within a process triggers the one-shot migration from
    legacy paths; subsequent calls return immediately.
    """
    global _MIGRATION_DONE
    root = _resolve_root()
    if not _MIGRATION_DONE:
        _migrate_legacy_locations(root)
        _MIGRATION_DONE = True
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Even mkdir failures shouldn't kill the launch — the caller
        # will get an obvious error when they try to write into a
        # directory that doesn't exist, and the actual problem (full
        # disk, network drive offline) is the user-visible one.
        pass
    return root


def cache_dir() -> Path:
    """Return the candle-cache directory (alias for :func:`app_data_dir`).

    The candle pickles historically lived directly at the data root
    (not in a ``cache/`` subdirectory) and the smoke harness asserts
    that ``disk_cache._cache_dir()`` equals ``TRADINGLAB_CACHE_DIR``
    exactly when the legacy env var is set. Routing
    :func:`disk_cache._cache_dir` here keeps that invariant intact
    while still letting events / logs / indicators / tokens get their
    own subdirectories.
    """
    return app_data_dir()


def logs_dir() -> Path:
    """Return ``<app_data_dir>/logs`` (created-if-missing).

    Used by :mod:`tradinglab.status` for daily log files and by the
    crash handler for ``crash-YYYY-MM-DDTHH-MM-SS.txt`` dumps.
    """
    d = app_data_dir() / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def events_dir() -> Path:
    """Return ``<app_data_dir>/events`` (created-if-missing).

    Used by :mod:`tradinglab.events.cache` for ``EventBundle``
    pickles (earnings / dividends / corporate-action histories).
    """
    d = app_data_dir() / "events"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def indicators_dir() -> Path:
    """Return ``<app_data_dir>/indicators`` (created-if-missing).

    Used by :mod:`tradinglab.indicators.loader` for user-authored
    indicator factory ``.py`` plugins.
    """
    d = app_data_dir() / "indicators"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def tokens_dir(*, override: Optional[str] = None) -> Path:
    """Return ``<app_data_dir>/tokens`` (created-if-missing).

    Used by :mod:`tradinglab.data.schwab_auth` for OAuth refresh-
    token cache files. The ``override`` kwarg (or the
    ``TRADINGLAB_TOKEN_DIR`` env var) lets test harnesses point this
    at a tempdir without touching the global data root override.
    """
    env_override = override or os.environ.get("TRADINGLAB_TOKEN_DIR")
    if env_override:
        d = Path(env_override)
    else:
        d = app_data_dir() / "tokens"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def reset_migration_flag_for_tests() -> None:
    """Re-arm the one-shot migration. Tests-only.

    The migration is idempotent so re-arming is safe; this exists so a
    test can verify the migration path under controlled inputs without
    spawning a subprocess.
    """
    global _MIGRATION_DONE
    _MIGRATION_DONE = False


__all__ = [
    "app_data_dir",
    "cache_dir",
    "logs_dir",
    "events_dir",
    "indicators_dir",
    "tokens_dir",
    "reset_migration_flag_for_tests",
    "_purge_legacy_pickle_caches",
    "wipe_yfinance_timezone_cache",
]
