"""One-shot profiler for the smoke test runner.

Wraps every ``check_*`` callable in :mod:`tests.smoke.test_smoke_full`
with a timer, runs ``main()``, then prints the slowest checks.

Run with::

    python -m tests.smoke._profile_smoke

No test file changes; safe to keep around as a utility.
"""
from __future__ import annotations

import time
import sys
from typing import Callable, List, Tuple

import tests.smoke.test_smoke_full as smoke


_TIMINGS: List[Tuple[str, float]] = []


def _wrap(name: str, fn: Callable) -> Callable:
    def _timed(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            _TIMINGS.append((name, time.perf_counter() - t0))
    _timed.__name__ = fn.__name__
    return _timed


def _install() -> None:
    for attr in dir(smoke):
        if not attr.startswith("check_"):
            continue
        obj = getattr(smoke, attr)
        if not callable(obj):
            continue
        setattr(smoke, attr, _wrap(attr, obj))


def _report() -> None:
    print("\n=== smoke profile (top 20 by wall time) ===")
    total = sum(t for _, t in _TIMINGS)
    print(f"total instrumented time: {total:.2f}s across {len(_TIMINGS)} checks")
    for name, secs in sorted(_TIMINGS, key=lambda x: -x[1])[:20]:
        bar = "#" * int(secs * 20)
        print(f"  {secs:6.3f}s  {name:<55} {bar}")


def main() -> int:
    _install()
    rc = smoke.main()
    _report()
    return rc


if __name__ == "__main__":
    sys.exit(main())
