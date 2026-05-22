"""In-memory drawing store + JSON persistence (Feature C).

The store is the single source of truth for the user's drawings
during a session. It exposes an :class:`IndicatorManager`-shaped
event bus so :class:`tradinglab.app.ChartApp` can listen for
changes and trigger a coalesced re-render at idle.

Persistence shape mirrors ``backtest.sandbox_resume`` line for
line — atomic tempfile + ``os.replace``, format-version envelope,
silent on OS errors. Drawing-list loss is preferable to a crash
on the close path.

File location: ``<app_data_dir>/drawings.json``, alongside
``settings.json`` / ``watchlists.json`` / ``sandbox_last.json``.
"""
from __future__ import annotations

import builtins
import datetime as _dt
import json
import os
import tempfile
import weakref
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..core.thread_guard import require_tk_thread
from .model import Drawing, normalize_ticker

DRAWINGS_FILE_FORMAT = "tradinglab-drawings"
DRAWINGS_FILE_VERSION = 1
DRAWINGS_FILE_NAME = "drawings.json"


# Subscriber callback shape:
#   ``cb(event_kind, ticker, drawing)``
#
# ``ticker`` is ``None`` for events that aren't ticker-scoped
# (``"loaded"`` / ``"clear_all"``). ``drawing`` is ``None`` for
# multi-target events (``"clear_symbol"`` / ``"clear_all"`` /
# ``"loaded"``).
Subscriber = Callable[[str, str | None, Drawing | None], None]
Scheduler = Callable[[Callable[[], None]], None]


# Weak registry of live ``DrawingStore`` instances. Lets the module-level
# :func:`clear_drawings` helper notify any subscribers in this process
# (e.g. the chart renderer) before deleting the on-disk file — without
# pinning stores in memory after the app has discarded them.
_live_stores: weakref.WeakSet[DrawingStore] = weakref.WeakSet()  # noqa: F821


# ---------------------------------------------------------------
# Module-level persistence helpers (importable by tests + the
# defensive flush in ``ChartApp._on_close``).
# ---------------------------------------------------------------

def drawings_file_path() -> Path:
    """Canonical on-disk path of the drawings file.

    Lazily resolves :func:`tradinglab.paths.app_data_dir` so test
    overrides via ``TRADINGLAB_DATA_DIR`` / monkeypatching take
    effect at every call.
    """
    from ..paths import app_data_dir
    return app_data_dir() / DRAWINGS_FILE_NAME


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()


def read_drawings() -> dict[str, list[Drawing]]:
    """Load drawings from disk, grouped by normalized ticker.

    Returns ``{}`` for any of:

    * file missing,
    * file not valid JSON,
    * format / version envelope mismatch,
    * payload structure unrecognised.

    Future-version files are **preserved on disk** (not deleted)
    so a downgrade or schema-aware migration can inspect them
    later. The current process simply gets an empty store. Garbage
    per-drawing entries are skipped silently without dropping the
    whole file.
    """
    path = drawings_file_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("format") != DRAWINGS_FILE_FORMAT:
        return {}
    if payload.get("version") != DRAWINGS_FILE_VERSION:
        return {}
    raw = payload.get("drawings_by_ticker")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[Drawing]] = {}
    seen_ids: set[str] = set()
    for tkr, items in raw.items():
        if not isinstance(items, list):
            continue
        key = normalize_ticker(tkr)
        if not key:
            continue
        drawings: list[Drawing] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                d = Drawing.from_dict(item)
            except (TypeError, ValueError):
                continue
            # Self-heal: rewrite the drawing's ticker to match the
            # bucket key if a hand-edited file has them out of sync.
            if d.ticker != key:
                d = d.replace(ticker=key)
            # Skip duplicate ids cross-ticker — store lookups (get,
            # update, remove) walk all buckets and would only ever
            # find the first occurrence, leaving the duplicates
            # un-deletable from the UI. Keep the first, drop the
            # rest. Audit ``drawing-duplicate-id``.
            if d.id in seen_ids:
                continue
            seen_ids.add(d.id)
            drawings.append(d)
        if drawings:
            out[key] = drawings
    return out


def _peek_file_version() -> int | None:
    """Return the integer ``version`` from a parseable envelope, else ``None``.

    Used by ``write_drawings`` and ``clear_drawings`` to detect a
    future-version file and refuse to clobber it. Returns ``None``
    for missing file, corrupt JSON, foreign ``format`` field, or
    non-integer ``version`` — those cases all go through the
    normal write path (the existing file is unrecoverable from a
    v1 perspective anyway).

    Audit ``drawings-future-version``.
    """
    path = drawings_file_path()
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("format") != DRAWINGS_FILE_FORMAT:
        return None
    ver = payload.get("version")
    if not isinstance(ver, int):
        return None
    return ver


def write_drawings(by_ticker: dict[str, list[Drawing]]) -> OSError | None:
    """Persist ``by_ticker`` atomically.

    The full ``drawings_by_ticker`` map is rewritten on each call;
    incremental writes would risk diverging from the in-memory
    snapshot under partial failure.

    Returns ``None`` on success, or the caught :class:`OSError`
    on failure. The function never raises — losing a write must
    not kill the close path — but the return value lets callers
    (``DrawingStore.flush``) surface the failure to the user via
    the save-error subscriber bus (audit
    ``os-replace-error-feedback``). Pre-fix the bare
    ``except OSError: pass`` made silent data loss the default
    on disk-full / OneDrive-lock / AV-block scenarios; the user
    saw the line on the chart, closed the app, reopened it, and
    the line was gone with no explanation.

    Empty per-ticker lists are dropped from the on-disk payload
    so the file shrinks naturally when the user removes the last
    line for a ticker.

    If an existing on-disk file declares a **future** version
    (``> DRAWINGS_FILE_VERSION``), the write is silently skipped
    and ``None`` is returned: ``read_drawings`` returned ``{}``
    for that file (signalling the in-memory store is empty), so
    a naive write would overwrite a v2/v3 file with our v1 empty
    payload — destroying user data the moment they open the app
    on an older build. Audit ``drawings-future-version``.
    """
    existing_version = _peek_file_version()
    if existing_version is not None and existing_version > DRAWINGS_FILE_VERSION:
        return None
    path = drawings_file_path()
    payload = {
        "format": DRAWINGS_FILE_FORMAT,
        "version": DRAWINGS_FILE_VERSION,
        "saved_at": _now_iso(),
        "drawings_by_ticker": {
            tkr: [d.to_dict() for d in items]
            for tkr, items in sorted(by_ticker.items())
            if items
        },
    }
    tmp_path: Path | None = None
    caught: OSError | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, indent=2, sort_keys=True)
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(path.parent),
                delete=False, suffix=".tmp") as tmp:
            # Capture the path immediately so the ``finally`` cleanup
            # can unlink a partial file if ``tmp.write`` or the
            # subsequent ``os.replace`` fails. Audit
            # ``tempfile-orphan-cleanup``.
            tmp_path = Path(tmp.name)
            tmp.write(data)
            tmp.flush()
            try:
                os.fsync(tmp.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(path))
        # Successful rename consumed the tempfile; don't try to
        # unlink the (now-nonexistent) original path in ``finally``.
        tmp_path = None
    except OSError as exc:
        # Capture the error so the caller can surface it. The
        # function still does not raise — the close path must
        # not crash on a flush failure. Audit
        # ``os-replace-error-feedback``.
        caught = exc
    finally:
        # On any failure, unlink the leftover tempfile to keep the
        # app-data directory tidy. Pre-fix, repeated AV-blocked
        # ``os.replace`` calls would litter ``%APPDATA%`` with
        # ``tmpXXXX.tmp`` orphans. Audit ``tempfile-orphan-cleanup``.
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return caught


def clear_drawings() -> None:
    """Clear All Drawings — both in-memory (live stores) and on disk.

    Fires ``("clear_all", None, None)`` on every :class:`DrawingStore`
    instance currently alive in this process before deleting the
    on-disk file. That keeps any subscribed renderer (e.g.
    :meth:`ChartApp._on_drawing_event`) in sync — an earlier
    revision deleted the file silently, leaving the live store +
    chart with stale lines until the user manually closed the app
    (audit ``clear-drawings-event-bus``, fixed 2026-05).

    Stores tracked via a :class:`weakref.WeakSet`, so this helper
    does not pin stores in memory. ``clear_all()`` on a store that
    is autosaving will schedule its own save, which then writes
    an empty payload; the final ``unlink`` is the belt-and-braces
    that handles the no-live-store / autosave-disabled cases too.
    Idempotent; safe to call from tests and one-shot CLI cleanup
    where no store instance exists.

    If the on-disk file is a **future** version, the file is left
    intact so a downgrade or schema migration can still recover
    it. The in-memory live stores (which were empty in this case
    because ``read_drawings`` returned ``{}``) are still cleared
    for consistency. Audit ``drawings-future-version``.
    """
    # Iterate a list copy: the WeakSet could mutate mid-loop if a
    # store is collected. Per-store errors are swallowed so a single
    # broken subscriber doesn't block the on-disk cleanup.
    for store in list(_live_stores):
        try:
            store.clear_all()
        except Exception:  # noqa: BLE001
            pass
    existing_version = _peek_file_version()
    if existing_version is not None and existing_version > DRAWINGS_FILE_VERSION:
        return
    try:
        drawings_file_path().unlink(missing_ok=True)
    except (OSError, TypeError):
        # TypeError covers ``unlink(missing_ok=)`` on older Python;
        # we're 3.10+ but be defensive.
        pass


# ---------------------------------------------------------------
# DrawingStore
# ---------------------------------------------------------------

class DrawingStore:
    """Owns the active drawing list + fires observer callbacks.

    The shape mirrors :class:`tradinglab.indicators.config.IndicatorManager`:
    a list of subscribers fired on every mutation, plus an optional
    scheduler used to coalesce disk writes to one per Tk idle tick.

    Threading model
    ---------------
    Mutating methods (``add``, ``remove``, ``update``,
    ``clear_symbol``, ``clear_all``, ``replace_all``) must be
    called from the Tk main thread. The check is **enforced**
    by ``@require_tk_thread`` on each mutator; off-main-thread
    invocation raises ``TkThreadViolation`` (audit
    ``drawing-thread-safety``). Read-only methods
    (``list``, ``get``, ``__len__``) and the subscriber-list
    operations (``subscribe`` / ``unsubscribe``) are not checked
    so background diagnostics can poll the store, but they
    remain best-effort under concurrent mutation (which the
    main-thread enforcement prevents anyway).

    Subscribers fire synchronously on the calling thread (which
    is always the main thread once the mutation guard is in
    place); subscribers that touch Tk widgets should still route
    through their own scheduler (``ChartApp._on_drawing_event``
    does this via :meth:`tkinter.Misc.after_idle`).

    Persistence
    -----------
    Every mutation schedules **one** disk write at idle via
    ``scheduler``. If ``scheduler`` is ``None`` (the test default),
    the write happens synchronously inside the mutating call. Tests
    that want explicit control can pass ``autosave=False`` and call
    :meth:`flush` themselves.

    Subscriber event shapes
    -----------------------
    * ``("add", ticker, drawing)`` — single drawing added.
    * ``("remove", ticker, drawing)`` — single drawing removed.
    * ``("update", ticker, drawing)`` — fields edited. ``ticker``
      is the **new** ticker if the update moved the drawing.
    * ``("clear_symbol", ticker, None)`` — every drawing for
      ``ticker`` removed.
    * ``("clear_all", None, None)`` — every drawing removed.
    * ``("loaded", None, None)`` — :meth:`replace_all` finished
      (e.g. on startup-load from disk).
    """

    def __init__(
        self,
        *,
        scheduler: Scheduler | None = None,
        autosave: bool = True,
    ) -> None:
        self._by_ticker: dict[str, list[Drawing]] = {}
        self._subscribers: list[Subscriber] = []
        # Monotonic revision counter bumped on every mutation
        # ({add,remove,update,clear_symbol,clear_all,loaded}). Used
        # by :class:`InteractionMixin` to invalidate its pick cache
        # without re-walking the store on every 60 Hz hover frame.
        # Audit ``pick-event-throttle``.
        self._revision: int = 0
        # Separate registry for save-error callbacks. Decoupled
        # from the mutation-event subscribers because (a) the
        # signature is different (single OSError arg) and (b)
        # subscribing to mutations does not imply caring about
        # save errors. Audit ``os-replace-error-feedback``.
        self._save_error_subscribers: list[Callable[[OSError], None]] = []
        self._scheduler = scheduler
        self._autosave = autosave
        self._save_pending = False
        # Register in the weak registry so the module-level
        # ``clear_drawings()`` can fire our event bus before unlinking
        # the on-disk file (audit ``clear-drawings-event-bus``).
        _live_stores.add(self)

    # ---- subscribers ---------------------------------------------

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register a change callback. Returns an unsubscribe handle."""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass
        return _unsubscribe

    def subscribe_save_errors(
        self,
        callback: Callable[[OSError], None],
    ) -> Callable[[], None]:
        """Register a save-failure callback. Returns an unsubscribe handle.

        Fires once per failed ``flush()`` (one mutation = at most
        one error). Callbacks receive the caught :class:`OSError`
        and should surface a user-visible message (status bar /
        toast) — the store deliberately does NOT crash on save
        failure, so this is the only way the UI learns about it.

        Per-callback errors are swallowed so a broken handler
        can't suppress the save-write itself or other callbacks.
        Audit ``os-replace-error-feedback``.
        """
        self._save_error_subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._save_error_subscribers.remove(callback)
            except ValueError:
                pass
        return _unsubscribe

    def _notify(
        self,
        event: str,
        ticker: str | None,
        drawing: Drawing | None,
    ) -> None:
        # Bump the revision FIRST so any subscriber that re-reads
        # the store (or calls :meth:`revision` for cache-key
        # invalidation) sees a fresh counter. Audit
        # ``pick-event-throttle``.
        self._revision += 1
        # Snapshot so that a callback adding/removing subscribers
        # doesn't corrupt iteration. Per-subscriber errors are
        # swallowed (subscribers' responsibility to handle their
        # own failures — the redraw hook logs to status).
        for cb in list(self._subscribers):
            try:
                cb(event, ticker, drawing)
            except Exception:  # noqa: BLE001
                pass

    # ---- accessors -----------------------------------------------

    def revision(self) -> int:
        """Monotonic mutation counter.

        Bumped on every event that ``_notify`` fires (add, remove,
        update, clear_symbol, clear_all, loaded). Hover-throttled
        callers cache pick results keyed by ``revision`` so a
        stationary cursor that's already done its single linear
        scan never re-scans. Audit ``pick-event-throttle``.
        """
        return self._revision

    def count(self, ticker: str) -> int:
        """O(1) drawing count for ``ticker`` without a list copy.

        Hover-rate callers want to short-circuit the entire
        hit-test path the moment the bucket is empty —
        ``len(store.list(ticker)) == 0`` allocates a fresh list
        every frame. Audit ``pick-event-throttle``.
        """
        key = normalize_ticker(ticker)
        return len(self._by_ticker.get(key, ()))

    def list(self, ticker: str) -> builtins.list[Drawing]:
        """Drawings registered against ``ticker`` (normalized)."""
        key = normalize_ticker(ticker)
        return list(self._by_ticker.get(key, ()))

    def all(self) -> dict[str, builtins.list[Drawing]]:
        """Shallow copy of the whole store, keyed by normalized ticker."""
        return {k: list(v) for k, v in self._by_ticker.items()}

    def get(self, drawing_id: str) -> tuple[str, Drawing] | None:
        """Look up a drawing across all tickers.

        Returns ``(ticker, drawing)`` or ``None``. Used by the
        right-click context menu's "Delete this line" path where
        we only have the artist's ``gid``.
        """
        for tkr, items in self._by_ticker.items():
            for d in items:
                if d.id == drawing_id:
                    return tkr, d
        return None

    def tickers(self) -> builtins.list[str]:
        """List of tickers that currently own at least one drawing."""
        return [t for t, items in self._by_ticker.items() if items]

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_ticker.values())

    # ---- mutation ------------------------------------------------

    @require_tk_thread
    def add(self, drawing: Drawing) -> Drawing:
        """Insert ``drawing``. Returns the stored instance (which
        may differ from the argument by normalized ticker).

        Raises :class:`ValueError` if the drawing's id collides
        with an existing drawing's id in any bucket — store
        lookups (``get`` / ``update`` / ``remove``) match by id
        and would only ever resolve the first occurrence. Callers
        should always allocate fresh ids via the
        :func:`make_hline_drawing` factory (the default empty
        ``drawing_id`` triggers a UUIDv4). Audit
        ``drawing-duplicate-id``.

        Raises :class:`TkThreadViolation` if called off the Tk main
        thread. Audit ``drawing-thread-safety``.
        """
        if self._find_id(drawing.id) is not None:
            raise ValueError(f"duplicate drawing id: {drawing.id!r}")
        key = normalize_ticker(drawing.ticker)
        if key != drawing.ticker:
            drawing = drawing.replace(ticker=key)
        self._by_ticker.setdefault(key, []).append(drawing)
        self._notify("add", key, drawing)
        self._schedule_save()
        return drawing

    def _find_id(self, drawing_id: str) -> tuple[str, int] | None:
        """Return ``(bucket_key, index)`` of the drawing with id
        ``drawing_id``, or ``None``."""
        for tkr, items in self._by_ticker.items():
            for i, d in enumerate(items):
                if d.id == drawing_id:
                    return (tkr, i)
        return None

    @require_tk_thread
    def remove(self, drawing_id: str) -> bool:
        """Remove the drawing with id ``drawing_id`` across all
        tickers. Returns True if found and removed.

        Raises :class:`TkThreadViolation` if called off the Tk main
        thread. Audit ``drawing-thread-safety``.
        """
        for tkr, items in list(self._by_ticker.items()):
            for i, d in enumerate(items):
                if d.id == drawing_id:
                    items.pop(i)
                    if not items:
                        del self._by_ticker[tkr]
                    self._notify("remove", tkr, d)
                    self._schedule_save()
                    return True
        return False

    @require_tk_thread
    def update(self, drawing_id: str, **changes: Any) -> Drawing | None:
        """Apply ``changes`` to the drawing with id ``drawing_id``.

        Returns the updated drawing, or ``None`` if no drawing
        with that id exists. If ``ticker`` is among the changes
        and resolves to a different normalized key, the drawing
        is moved between buckets atomically.

        Raises :class:`TkThreadViolation` if called off the Tk main
        thread. Audit ``drawing-thread-safety``.
        """
        for tkr, items in list(self._by_ticker.items()):
            for i, d in enumerate(items):
                if d.id == drawing_id:
                    nd = d.replace(**changes)
                    new_key = normalize_ticker(nd.ticker)
                    if new_key != tkr:
                        items.pop(i)
                        if not items:
                            del self._by_ticker[tkr]
                        self._by_ticker.setdefault(new_key, []).append(nd)
                    else:
                        items[i] = nd
                    self._notify("update", new_key, nd)
                    self._schedule_save()
                    return nd
        return None

    @require_tk_thread
    def clear_symbol(self, ticker: str) -> int:
        """Remove every drawing for ``ticker``. Returns the count
        removed (0 if the ticker had none).

        Raises :class:`TkThreadViolation` if called off the Tk main
        thread. Audit ``drawing-thread-safety``.
        """
        key = normalize_ticker(ticker)
        items = self._by_ticker.pop(key, None)
        if not items:
            return 0
        self._notify("clear_symbol", key, None)
        self._schedule_save()
        return len(items)

    @require_tk_thread
    def clear_all(self) -> int:
        """Remove every drawing for every ticker. Returns the
        total count removed.

        Raises :class:`TkThreadViolation` if called off the Tk main
        thread. Audit ``drawing-thread-safety``.
        """
        total = sum(len(v) for v in self._by_ticker.values())
        if total == 0:
            return 0
        self._by_ticker.clear()
        self._notify("clear_all", None, None)
        self._schedule_save()
        return total

    @require_tk_thread
    def replace_all(
        self,
        by_ticker: dict[str, Iterable[Drawing]],
    ) -> None:
        """Reset internal state to ``by_ticker``.

        Used on startup to seed the store from disk. Fires a
        single ``"loaded"`` event. Does **not** trigger a save —
        the data just came from disk; persisting it again would
        be wasted I/O. Subsequent mutations will persist normally.

        Raises :class:`TkThreadViolation` if called off the Tk main
        thread. Audit ``drawing-thread-safety``.
        """
        fresh: dict[str, list[Drawing]] = {}
        seen_ids: set[str] = set()
        for tkr, items in by_ticker.items():
            key = normalize_ticker(tkr)
            if not key:
                continue
            lst: list[Drawing] = []
            for d in items:
                if normalize_ticker(d.ticker) != key:
                    d = d.replace(ticker=key)
                # Belt-and-braces dedup: read_drawings already
                # dedupes, but a caller may hand us their own
                # dict. Audit ``drawing-duplicate-id``.
                if d.id in seen_ids:
                    continue
                seen_ids.add(d.id)
                lst.append(d)
            if lst:
                fresh[key] = lst
        self._by_ticker = fresh
        self._notify("loaded", None, None)

    # ---- persistence ---------------------------------------------

    def _schedule_save(self) -> None:
        if not self._autosave:
            return
        if self._save_pending:
            return
        self._save_pending = True

        def _fire() -> None:
            self._save_pending = False
            self.flush()

        if self._scheduler is None:
            _fire()
            return
        try:
            self._scheduler(_fire)
        except Exception:  # noqa: BLE001
            # Scheduler failed (no mainloop yet) — flush now.
            _fire()

    def flush(self) -> None:
        """Write the current state to disk immediately. Idempotent.

        Called by :meth:`_schedule_save` at idle and explicitly
        from :meth:`tradinglab.app.ChartApp._on_close` as a
        defensive synchronous flush in case the last coalesced
        write hadn't fired yet.

        On failure the captured :class:`OSError` is dispatched to
        every callback registered via
        :meth:`subscribe_save_errors`. The method itself never
        raises — losing a write must not kill the close path —
        but the subscribers turn the silent failure into a
        user-visible status message. Audit
        ``os-replace-error-feedback``.
        """
        err = write_drawings(self.all())
        if err is not None:
            self._notify_save_error(err)

    def _notify_save_error(self, exc: OSError) -> None:
        """Dispatch ``exc`` to every save-error subscriber.

        Snapshots the subscriber list before iteration so a
        callback that subscribes/unsubscribes during dispatch
        doesn't corrupt iteration. Per-callback errors are
        swallowed (the store can't decide whether the UI's
        status bar is alive).
        """
        for cb in list(self._save_error_subscribers):
            try:
                cb(exc)
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "DRAWINGS_FILE_FORMAT",
    "DRAWINGS_FILE_NAME",
    "DRAWINGS_FILE_VERSION",
    "DrawingStore",
    "Scheduler",
    "Subscriber",
    "clear_drawings",
    "drawings_file_path",
    "read_drawings",
    "write_drawings",
]
