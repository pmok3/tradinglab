# gui/update_check.py — Spec

## Purpose

`UpdateCheckMixin` extracted from `ChartApp`. Owns the **"update
available"** UX surface — the async update-check result handler and
the passive one-line banner that surfaces a new release version
under the chart title bar.

The CHECK-TRIGGERING call site (``updates.schedule_check_async(
self.after, self._on_update_check_result, force=False)``) stays in
``app.py``; the mixin only handles the result + paints the banner.

## Public API

### `UpdateCheckMixin` methods (bound on `ChartApp`)

- `_on_update_check_result(result) -> None` — Tk-main-thread
  callback for ``updates.schedule_check_async``. No-op unless
  ``result.status == "available"`` and ``result.latest`` is
  non-empty; on that path, calls ``_show_update_banner(latest,
  url=result.url)``. Swallows every exception so a poll-time
  hiccup never breaks the chart.
- `_show_update_banner(new_version, *, url="") -> None` —
  build + pack the one-line dismissable ttk.Frame. Adds a
  "View release" button when ``url`` is non-empty. Idempotent:
  a second call while the banner is already visible is a no-op.

## State touched

- `self._update_banner_frame` (read via ``getattr(..., None)``
  guard, written from ``_show_update_banner`` and the nested
  ``_dismiss`` closure). Stores the live ``ttk.Frame`` while
  visible; ``None`` after dismiss / on construction failure.

## Dependencies

- External: `tkinter`, `tkinter.ttk`, `webbrowser`.
- Internal: none. Mirrors the
  :class:`gui.banner.FirstRunBannerMixin` pattern but kept
  separate because update-banner semantics (dismiss button +
  release link + idempotency on re-fire) diverge from the
  first-run banner's tutorial cadence.

## Design Decisions

- **No `__init__` on the mixin.** Relies on
  ``getattr(self, "_update_banner_frame", None)`` so the attribute
  is implicitly None until the first banner is shown. Matches the
  pattern already used at every read site.
- **Two layers of `try/except Exception`.** A failed
  ``getattr(result, ...)`` (e.g. result is None) and a failed
  ``ttk.Frame`` construction (e.g. Tk teardown race) both
  silently swallow — surfacing a Python traceback from the update
  check would defeat the purpose of a passive notification.
- **`_dismiss` closure resets `_update_banner_frame` to None** so
  a subsequent update notification can show its own banner.

## Invariants

- `_update_banner_frame` is either ``None`` or a live ``ttk.Frame``;
  never a destroyed widget.
- `_show_update_banner` never raises; banner construction failure
  resets the attribute to None instead of leaving it half-set.
- `_on_update_check_result` is safe to call from any thread that
  was scheduled via ``self.after``.
