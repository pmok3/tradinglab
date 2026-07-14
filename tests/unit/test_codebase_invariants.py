"""Codebase-wide invariant meta-tests.

These tests pin contracts that span the whole codebase (no specific
subsystem) and fire at PR time when a developer violates one. Each
contract has its own ``_..._EXEMPTIONS`` dict for documented
grandfathered cases.

Contracts in this file:

1. **Spec.md coverage** — every ``.py`` under ``src/tradinglab/``
   (excluding ``__init__.py``) has a colocated ``.spec.md``. HARD
   RULE per AGENTS.md §2 — this is the first CI gate enforcing it.

2. **ChartApp MRO structural invariants** (per AGENTS.md §7.24):
   - ``tk.Tk`` is the LAST base of ``ChartApp``.
   - No mixin defines ``__init__`` (adding one breaks the MRO
     chain to ``tk.Tk`` which is positional-arg sensitive).
   - ``app.py`` stays within its LOC ceiling (ratchets down only)
     so the god-object can't silently regrow after an extraction.

3. **TriggerKind dispatch completeness** (per AGENTS.md §7.20):
   - Every member of ``entries.model.TriggerKind`` is a key in
     ``entries.dispatch._ENTRY_DISPATCH``.
   - Every member of ``exits.model.TriggerKind`` is a key in
     ``exits.dispatch._EXIT_DISPATCH``.
   Adding a new TriggerKind without registering a handler would
   silently produce "fires nothing" in both live + mechanical
   evaluators — the worst kind of regression.

4. **``ZoneInfo("America/New_York")`` only in ``core/timezones.py``**
   (per AGENTS.md §7.23): every ET lookup must go through the
   ``core.timezones`` helpers so the missing-tzdata fallback policy
   is uniform.

Audit ``codebase-invariants``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "tradinglab"


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def _all_py_files() -> list[Path]:
    return [
        p
        for p in sorted(_SRC.rglob("*.py"))
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    ]


# ---------------------------------------------------------------------------
# 1. Spec.md coverage
# ---------------------------------------------------------------------------


# Files that legitimately don't need a `.spec.md` (e.g. compiled/generated
# code or trivial shim modules). Add ONLY with a documented reason.
_SPEC_COVERAGE_EXEMPTIONS: dict[str, str] = {
    # (none today — every .py has its spec)
}


def test_every_module_has_colocated_spec_md():
    """HARD RULE per AGENTS.md §2: every `.py` under `src/tradinglab/`
    (excluding `__init__.py`) has a colocated `.spec.md`.

    Audit ``codebase-invariants``.
    """
    missing: list[str] = []
    for py in _all_py_files():
        rel = py.relative_to(_REPO_ROOT).as_posix()
        if rel in _SPEC_COVERAGE_EXEMPTIONS:
            continue
        if not py.with_suffix(".spec.md").exists():
            missing.append(rel)
    if missing:
        pytest.fail(
            "Modules without a colocated .spec.md (HARD RULE per "
            "AGENTS.md §2):\n  - " + "\n  - ".join(missing)
            + "\n\nCreate a sibling .spec.md (see docs/SPEC_STYLE.md) "
            "OR add to _SPEC_COVERAGE_EXEMPTIONS with a reason."
        )


def test_no_orphan_spec_md_files():
    """Every `.spec.md` must have a sibling `.py` file. Catches stale
    specs left behind when a module is renamed or removed.
    """
    orphans: list[str] = []
    for md in sorted(_SRC.rglob("*.spec.md")):
        if "__pycache__" in md.parts:
            continue
        # Top-level spec.md (one per phase) is allowed without a sibling .py.
        if md.name == "spec.md":
            continue
        py = md.with_suffix("").with_suffix(".py")
        if not py.exists():
            orphans.append(md.relative_to(_REPO_ROOT).as_posix())
    if orphans:
        pytest.fail(
            "Orphan .spec.md files (no sibling .py):\n  - "
            + "\n  - ".join(orphans)
            + "\n\nRemove them OR rename to match the .py file."
        )


# ---------------------------------------------------------------------------
# 2. ChartApp MRO structural invariants
# ---------------------------------------------------------------------------


def test_chartapp_mixins_have_no_init_method():
    """Per AGENTS.md §7.24, no mixin in the ChartApp MRO may define
    ``__init__`` or call ``super().__init__()``. All instance state
    lives in ``ChartApp.__init__``; adding ``__init__`` to a mixin
    breaks the MRO chain at ``tk.Tk`` (which is positional-arg
    sensitive).
    """
    import tkinter as tk

    from tradinglab.app import ChartApp

    offenders: list[str] = []
    for base in ChartApp.__bases__:
        if base is tk.Tk:
            continue
        if "__init__" in base.__dict__:
            offenders.append(f"{base.__module__}.{base.__name__}")
    if offenders:
        pytest.fail(
            "Mixins with __init__ (would break MRO chaining to tk.Tk):"
            "\n  - " + "\n  - ".join(offenders)
            + "\n\nMove the state to ChartApp.__init__ and remove the "
            "mixin's __init__ method."
        )


def test_chartapp_last_base_is_tk_tk():
    """Per AGENTS.md §7.24, ``tk.Tk`` MUST stay the final base in
    ``ChartApp.__bases__``. Anything else there would break the Tk
    bootstrap.
    """
    import tkinter as tk

    from tradinglab.app import ChartApp

    last = ChartApp.__bases__[-1]
    assert last is tk.Tk, (
        f"ChartApp's last base is {last.__name__}, not tk.Tk — this "
        "breaks the Tk bootstrap. Reorder __bases__ so tk.Tk is last."
    )


def test_app_spec_md_mro_matches_real_chartapp_bases():
    """The ``class ChartApp(...)`` declaration in ``app.spec.md`` MUST
    list exactly the real ``ChartApp.__bases__`` (same names, same
    order). Guards the exact spec drift found in the 2026-06 audit, where
    wave-3 added ``SandboxAppMixin`` / ``ScannerAppMixin`` to the class
    but not to the spec declaration. The other MRO gates check the CODE
    side; this one pins the SPEC side so the two can't diverge silently.

    Audit ``codebase-invariants``.
    """
    import re

    from tradinglab.app import ChartApp

    real = [b.__name__ for b in ChartApp.__bases__]  # tk.Tk -> "Tk"

    spec = (_SRC / "app.spec.md").read_text(encoding="utf-8")
    match = re.search(r"class ChartApp\((.*?)\)", spec, flags=re.DOTALL)
    assert match, (
        "app.spec.md must document the `class ChartApp(...)` MRO line"
    )
    # Normalise: the spec writes the final base as ``tk.Tk`` while
    # ``__name__`` is ``Tk`` — compare on the last dotted component.
    spec_bases = [
        tok.strip().split(".")[-1]
        for tok in match.group(1).split(",")
        if tok.strip()
    ]
    assert spec_bases == real, (
        "app.spec.md ChartApp MRO is out of sync with the real class.\n"
        f"  spec: {spec_bases}\n  code: {real}\n"
        "Update the `class ChartApp(...)` line in app.spec.md (and the "
        "§11/§12 mixin lists) to match src/tradinglab/app.py."
    )


# High-water mark for ``app.py`` line count, measured the SAME way this
# test measures it (``str.splitlines()``). ``app.py`` is a god-object under
# active mixin extraction (AGENTS.md §7.24): waves 1-3 cut it from 7790 to
# ~6036 LOC, but later feature work (Alpaca source, targeted intraday fetch,
# compare-mode fixes, ...) silently regrew it past 7800 with no guardrail —
# the exact regression this ceiling now prevents.
#
# RATCHET-DOWN ONLY. Shrinking app.py is always fine; GROWTH must bump this
# constant as a conscious, reviewed decision — and the right move is almost
# always to extract a mixin (WatchlistsAppMixin is the next candidate, §7.24)
# rather than raise the cap.
#
# Deliberate bumps (newest first):
#   7235 — fix: compare-toggle in a historical drilldown now time-preserves
#          BOTH directions (stops the "candles creep in from the left on
#          repeated toggle" bug). Net +1 line (bugfix, not regrowth).
#   7234 — extract ChartStackAppMixin (gui/chartstack_app.py, ~294 LOC) +
#          EventsAppMixin (gui/events_app.py, ~167 LOC): ChartStack sidebar
#          glue + event-glyph fetch/overlay moved out. RATCHET DOWN.
#   7702 — extract AnchorPickAppMixin (gui/anchor_pick_app.py): the AVWAP
#          "Pick Anchor…" click flow (4 methods, ~247 LOC) moved out of the
#          god-object. RATCHET DOWN (locks in the reduction).
#   7949 — source-switch view-preserve (prev-axis tracking + race guard) +
#          steady-state tick-refresh perf state (tick-readout-decouple +
#          repaint coalescing). Both are feature/bugfix logic, not regrowth.
#   7899 — perf item #1: on_axis_change partial-volume (IEX) warning.
#   7885 — initial high-water mark when the ratchet was introduced.
#   7283 — compare-toggle manual-pan creep fix: coverage-gated time-preserve
#          (_compare_cache_first_ts + _compare_cache_covers helpers, broadened
#          _on_compare_toggle gate). Bug fix logic, not regrowth.
#   7328 — compare-slot xlim-mirror fix: _compute_slot_window mirrors the
#          primary's EXACT applied float xlim on the compare slot (kills the
#          index-preserve floor/ceil left-creep under poll re-renders). Bug
#          fix logic, not regrowth.
#   7380 — load-complete readout now shows the loaded series' date range
#          (_series_date_span); surfaces a provider returning years-stale /
#          incomplete data. Diagnostic feature.
#   7394 — opt-in view diagnostics (TRADINGLAB_DEBUG_VIEW) log the per-render
#          preserve mode + resolved visible window. Silent unless env-set.
#   7417 — ticker-switch default-view alignment: _ticker_change_should_time_
#          preserve() gates time-preserve on is_historical so a watchlist
#          cycle / type / promote at the default view shows the NEW ticker's
#          own default window. Bug fix logic.
#   7429 — compare-toggle on a historical drilled view no longer force-
#          refetches the compare every toggle (gated on is_historical). Bug
#          fix logic.
#   7470 — compare-toggle with an EMPTY compare 5m cache now does a targeted
#          single-day fill on a range-capable provider (no full _load_data of
#          the compare's ~120-day history) + pins the compare ticker in the
#          cache trim/stash. Bug fix logic (compare-toggle-targeted-first-load
#          + compare-ticker-cache-pin).
#   7519 — source-switch view-preserve hardened: a durable
#          _pending_axis_switch_time_preserve + a re-assert of TIME-preserve
#          at the switch's completing _load_data render, so a mid-switch
#          index-preserve re-arm (poll-tick / Compare-prefetch render during
#          the async load) can't reinterpret the stale bar-index window and
#          jump the view years back. + class-level defaults for the two
#          axis-switch flags (bare-__new__ harness read-safety). Bug fix
#          logic (source-switch-view-preserve).
#   7541 — view-intent controller (core.view_intent.ViewController): the
#          scattered visible-X booleans became a thin bridging-property surface
#          over ONE controller that centralises one-shot consumption, the
#          by_time > index precedence, and the HOLD-during-async-switch rule
#          (generic replacement for _pending_axis_switch_time_preserve). Adds
#          ~53 LOC of bridging properties; net +22 after deleting the bespoke
#          d94 re-assertion + redundant inits. Refactor (view-intent-controller).
#   7548 — flagged background prefetch scheduler wired in (data/prefetch/*).
#          The bulk of the glue is EXTRACTED to gui/prefetch_app.PrefetchAppMixin
#          (§7.24 preferred); only the irreducible wiring remains in app.py: the
#          mixin import + MRO entry, the __init__ construction
#          (self._prefetch_driver = self._maybe_build_prefetch_driver()), and the
#          _prefetch_observe() hook in _on_explicit_axis_change. Initial rollout
#          was opt-in behind the env flag. Feature (prefetch-scheduler).
#   7557 — prefetch cut-over draft (prefetch-cutover branch): observe-hook
#          coverage — the _load_data_async chokepoint (_prefetch_observe_soon)
#          replaces the axis-change hook and covers ticker/watchlist/chart-stack
#          switches; plus compare-toggle + startup hooks. TEMPORARY high-water
#          mark: the flip stage removes the reactive compare / companion
#          prefetch paths, which drops app.py well below this — lower the
#          ceiling then.
#   7449 — prefetch scheduler flip: default live scheduler + removal of the
#          reactive compare and companion-interval OHLC prefetch paths from
#          app.py. The remaining _ensure_prefetched seam is retained only for
#          live polling / on-demand overlays outside the cut-over.
_APP_PY_LOC_CEILING = 7449

# Once a real extraction drops app.py well under the ceiling, lower the
# constant to lock the reduction in. The band keeps ordinary small edits from
# tripping the floor while forcing a ratchet after a genuine ~500+ LOC cut.
_APP_PY_LOC_RATCHET_BAND = 500


def test_app_py_stays_within_loc_ceiling():
    """``app.py`` must not silently regrow past its high-water mark.

    The mixin-extraction sprints (§7.24) exist to shrink the god-object;
    without a ceiling it refills invisibly (it grew ~1800 LOC past the
    documented 6036 post-wave-3 count before this gate landed). Fires at PR
    time so any growth is a deliberate, reviewed bump of
    ``_APP_PY_LOC_CEILING`` — ideally replaced by extracting a mixin.
    """
    app_py = _SRC / "app.py"
    loc = len(app_py.read_text(encoding="utf-8").splitlines())
    assert loc <= _APP_PY_LOC_CEILING, (
        f"app.py grew to {loc} LOC, over the ceiling of {_APP_PY_LOC_CEILING}. "
        "app.py is a god-object under active extraction (AGENTS.md §7.24) — "
        "prefer extracting a mixin (e.g. WatchlistsAppMixin) over growing it. "
        "If the growth is genuinely justified, raise _APP_PY_LOC_CEILING "
        "deliberately in tests/unit/test_codebase_invariants.py."
    )
    assert loc >= _APP_PY_LOC_CEILING - _APP_PY_LOC_RATCHET_BAND, (
        f"app.py shrank to {loc} LOC, far under the ceiling of "
        f"{_APP_PY_LOC_CEILING}. Lower _APP_PY_LOC_CEILING to lock in the "
        "reduction — this ceiling ratchets DOWN only."
    )


# ---------------------------------------------------------------------------
# 3. TriggerKind dispatch completeness
# ---------------------------------------------------------------------------


def test_every_entry_trigger_kind_has_dispatch_handler():
    """Per AGENTS.md §7.20: every member of
    ``entries.model.TriggerKind`` MUST have a handler entry in
    ``entries.dispatch._ENTRY_DISPATCH``. Both the live entry
    evaluator AND the strategy_tester mechanical evaluator delegate
    to this dict — a missing handler silently no-fires the trigger
    in BOTH live and mechanical paths.
    """
    from tradinglab.entries.dispatch import _ENTRY_DISPATCH
    from tradinglab.entries.model import TriggerKind

    missing = [
        tk for tk in TriggerKind if tk not in _ENTRY_DISPATCH
    ]
    if missing:
        pytest.fail(
            "TriggerKind members without an _ENTRY_DISPATCH handler "
            "(silent no-fire on both live + mechanical paths):"
            "\n  - " + "\n  - ".join(repr(m) for m in missing)
            + "\n\nRegister handlers in src/tradinglab/entries/dispatch.py."
        )


def test_every_exit_trigger_kind_has_dispatch_handler():
    """Per AGENTS.md §7.20: every member of
    ``exits.model.TriggerKind`` MUST have a handler entry in
    ``exits.dispatch._EXIT_DISPATCH``.
    """
    from tradinglab.exits.dispatch import _EXIT_DISPATCH
    from tradinglab.exits.model import TriggerKind

    missing = [
        tk for tk in TriggerKind if tk not in _EXIT_DISPATCH
    ]
    if missing:
        pytest.fail(
            "TriggerKind members without an _EXIT_DISPATCH handler "
            "(silent no-fire on both live + mechanical paths):"
            "\n  - " + "\n  - ".join(repr(m) for m in missing)
            + "\n\nRegister handlers in src/tradinglab/exits/dispatch.py."
        )


def test_no_orphan_entry_dispatch_handlers():
    """Every key in ``_ENTRY_DISPATCH`` must be a real TriggerKind
    member. Catches stale handler entries when a TriggerKind is
    renamed / removed.
    """
    from tradinglab.entries.dispatch import _ENTRY_DISPATCH
    from tradinglab.entries.model import TriggerKind

    members = set(TriggerKind)
    orphans = [k for k in _ENTRY_DISPATCH if k not in members]
    assert not orphans, (
        f"Stale entries in _ENTRY_DISPATCH (not in TriggerKind enum): "
        f"{orphans}"
    )


def test_no_orphan_exit_dispatch_handlers():
    """Every key in ``_EXIT_DISPATCH`` must be a real TriggerKind member."""
    from tradinglab.exits.dispatch import _EXIT_DISPATCH
    from tradinglab.exits.model import TriggerKind

    members = set(TriggerKind)
    orphans = [k for k in _EXIT_DISPATCH if k not in members]
    assert not orphans, (
        f"Stale entries in _EXIT_DISPATCH (not in TriggerKind enum): "
        f"{orphans}"
    )


# ---------------------------------------------------------------------------
# 6. ZoneInfo consolidation
# ---------------------------------------------------------------------------


# Files that may legitimately construct ZoneInfo("America/New_York") directly
# instead of importing from core.timezones. Add ONLY with a documented reason
# (per AGENTS.md §7.23 deferred-migration list).
_ZONEINFO_EXEMPTIONS: dict[str, str] = {
    "gui/sandbox_panel.py": (
        "Deferred migration site per AGENTS.md §7.23 — uses bespoke "
        "_get_tz_for_label fallback. TODO: collapse into core.timezones."
    ),
}


def test_no_direct_et_zoneinfo_outside_core_timezones():
    """Per AGENTS.md §7.23: the only place that may construct
    ``ZoneInfo("America/New_York")`` directly is ``core/timezones.py``.
    Every other ET lookup must go through ``core.timezones.ET`` /
    ``get_et()`` / ``now_et()`` / ``to_et()`` so the missing-tzdata
    fallback policy stays uniform.

    Grandfathered sites are in ``_ZONEINFO_EXEMPTIONS``; the
    preferred direction is to remove entries from that dict by
    migrating to ``core.timezones``.
    """
    target_substring = 'ZoneInfo("America/New_York")'
    findings: list[str] = []
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        # Allowed: the canonical source
        if rel == "core/timezones.py":
            continue
        text = py.read_text(encoding="utf-8")
        if target_substring not in text:
            continue
        if rel in _ZONEINFO_EXEMPTIONS:
            continue
        # Capture line numbers for clarity
        for i, line in enumerate(text.splitlines(), 1):
            if target_substring in line:
                findings.append(f"{rel}:{i}")
    if findings:
        pytest.fail(
            "Direct ZoneInfo('America/New_York') construction outside "
            "core/timezones.py:\n  - " + "\n  - ".join(findings)
            + "\n\nReplace with `from .core.timezones import ET` (and "
            "branch on `ET is None` for missing-tzdata) OR add to "
            "_ZONEINFO_EXEMPTIONS with a documented reason."
        )


def test_zoneinfo_exemptions_are_actually_present():
    """Catch stale entries in ``_ZONEINFO_EXEMPTIONS``."""
    target_substring = 'ZoneInfo("America/New_York")'
    present_files: set[str] = set()
    for py in _all_py_files():
        rel = py.relative_to(_SRC).as_posix()
        if rel == "core/timezones.py":
            continue
        if target_substring in py.read_text(encoding="utf-8"):
            present_files.add(rel)
    stale = sorted(
        f for f in _ZONEINFO_EXEMPTIONS if f not in present_files
    )
    assert not stale, (
        "Stale entries in _ZONEINFO_EXEMPTIONS (no longer construct "
        "ZoneInfo directly):\n  - " + "\n  - ".join(stale)
    )


# ---------------------------------------------------------------------------
# AST sanity check (helper)
# ---------------------------------------------------------------------------


def test_all_source_files_parse_as_valid_python():
    """Sanity: every `.py` under `src/tradinglab/` is parseable Python.
    Catches Windows-encoding mishaps, accidental binary saves, etc.
    """
    bad: list[tuple[str, str]] = []
    for py in _all_py_files():
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as e:
            bad.append((py.relative_to(_REPO_ROOT).as_posix(), str(e)))
    assert not bad, (
        "Files that failed to parse:\n  - "
        + "\n  - ".join(f"{f}: {e}" for f, e in bad)
    )
