"""``Clock`` — monotonic bar-index advancer over a master timeline.

The sandbox is multi-ticker but synchronous: at every "Next bar" click
all loaded tickers advance one bar in lockstep. The clock owns the
master timeline (an ``int64`` ndarray of epoch seconds) so there is one
authoritative answer to "what time is it now?" regardless of which
ticker is focused. Per-symbol :class:`BarSeries` are aligned at the
engine layer; the clock itself is symbol-agnostic.

The clock starts at ``index = -1`` (no bar visible). The first
``tick()`` lands ``index = 0``. ``is_exhausted`` is true when no
further tick will succeed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Clock:
    timeline: np.ndarray
    index: int = -1

    def __post_init__(self) -> None:
        if self.timeline.dtype != np.int64:
            raise TypeError(
                f"Clock.timeline must be int64; got {self.timeline.dtype!r}"
            )
        if self.timeline.ndim != 1:
            raise ValueError("Clock.timeline must be 1-D")

    def __len__(self) -> int:
        return int(self.timeline.shape[0])

    @property
    def is_started(self) -> bool:
        return self.index >= 0

    @property
    def is_exhausted(self) -> bool:
        return self.index + 1 >= len(self)

    @property
    def now_ts(self) -> int:
        if self.index < 0:
            return -1
        return int(self.timeline[self.index])

    def tick(self) -> bool:
        """Advance one bar. Returns True on success, False if exhausted."""
        if self.is_exhausted:
            return False
        self.index += 1
        return True
