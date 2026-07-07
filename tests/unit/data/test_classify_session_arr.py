"""Equivalence + memory contract for ``constants.classify_session_arr``.

The vectorized session classifier used by ``data.normalize.candles_from_dataframe``
MUST stay bit-for-bit identical to the scalar ``classify_session`` — the session
tag flows into ``Candle.session`` and is read by rendering, session shading, the
scanner, and the strategy tester. It must also reuse a small set of shared label
strings (not allocate one ``str`` per bar).
"""

from __future__ import annotations

import numpy as np

from tradinglab.constants import classify_session, classify_session_arr


def test_matches_scalar_across_full_day():
    """Every (hour, minute) of a full day maps identically to the scalar fn."""
    hours = np.repeat(np.arange(24), 60)
    minutes = np.tile(np.arange(60), 24)
    got = classify_session_arr(hours, minutes)
    expected = [
        classify_session(int(h), int(m))
        for h, m in zip(hours, minutes, strict=True)
    ]
    assert got == expected


def test_session_boundaries():
    hours = np.array([9, 9, 15, 16, 19, 20, 3])
    minutes = np.array([29, 30, 59, 0, 59, 0, 0])
    assert classify_session_arr(hours, minutes) == [
        "pre", "regular", "regular", "post", "post", "pre", "pre",
    ]


def test_returns_plain_python_str_not_numpy():
    got = classify_session_arr(np.array([10]), np.array([0]))
    assert type(got[0]) is str  # not numpy.str_ (which would break json.dumps)


def test_labels_are_shared_objects():
    """A full-day input spans all 3 sessions but must yield at most 3 distinct
    string objects — proof the labels are shared, not per-bar allocated."""
    hours = np.repeat(np.arange(24), 60)
    minutes = np.tile(np.arange(60), 24)
    got = classify_session_arr(hours, minutes)
    assert len({id(s) for s in got}) <= 3


def test_empty_input():
    assert classify_session_arr(np.array([], dtype=int), np.array([], dtype=int)) == []
