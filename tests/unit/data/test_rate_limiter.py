"""Unit tests for the token-bucket rate limiter (data/rate_limiter.py)."""

from __future__ import annotations

import threading

from tradinglab.data.rate_limiter import TokenBucket


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ---------------------------------------------------------------------------
# Sizing + refill math (deterministic, fake clock — no real sleeps)
# ---------------------------------------------------------------------------


def test_starts_full_with_clean_burst_capacity():
    # 200/min, safety 0.9 → refill 3/s, burst capacity = round(200*0.1) = 20.
    clk = _FakeClock()
    b = TokenBucket(200, clock=clk)
    got = sum(b.try_acquire() for _ in range(30))
    assert got == 20  # exactly the burst capacity, then empty
    assert b.try_acquire() is False


def test_continuous_refill():
    clk = _FakeClock()
    b = TokenBucket(200, clock=clk)  # refill 3 tokens/s
    # Drain the initial burst.
    while b.try_acquire():
        pass
    clk.advance(1.0)  # +3 tokens
    assert sum(b.try_acquire() for _ in range(5)) == 3
    clk.advance(2.0)  # +6 tokens
    assert sum(b.try_acquire() for _ in range(10)) == 6


def test_time_until_available():
    clk = _FakeClock()
    b = TokenBucket(200, clock=clk)  # 3/s
    while b.try_acquire():
        pass
    # Empty: one token is 1/3 s away.
    assert abs(b.time_until_available(1) - (1 / 3)) < 1e-6
    assert b.time_until_available(0) == 0.0


def test_refill_caps_at_capacity():
    clk = _FakeClock()
    b = TokenBucket(200, clock=clk)  # capacity 20
    clk.advance(10_000)  # would add 30k tokens if uncapped
    assert sum(b.try_acquire() for _ in range(1000)) == 20


def test_worst_case_stays_within_rolling_minute_budget():
    # THE correctness property: no more than `limit` requests in any rolling
    # 60 s window. Start full, drain, then greedily acquire across 60 s.
    clk = _FakeClock()
    limit = 200
    b = TokenBucket(limit, clock=clk)
    total = 0
    # t=0: drain the initial burst.
    while b.try_acquire():
        total += 1
    # Advance in 0.1 s steps to t=60, greedily consuming.
    for _ in range(600):
        clk.advance(0.1)
        while b.try_acquire():
            total += 1
    assert total <= limit  # never exceeds the per-minute budget
    assert total >= limit - 1  # and uses ~all of it (tight)


# ---------------------------------------------------------------------------
# Live reconfiguration (free ↔ paid tier change without restart)
# ---------------------------------------------------------------------------


def test_configure_changes_rate_live():
    clk = _FakeClock()
    b = TokenBucket(200, clock=clk)
    assert b.rate_per_min == 200
    b.configure(10000)  # upgrade to paid
    assert b.rate_per_min == 10000
    # New refill ≈ 150/s (10000*0.9/60); drain then +1 s yields ~150.
    while b.try_acquire():
        pass
    clk.advance(1.0)
    got = sum(b.try_acquire() for _ in range(200))
    assert 140 <= got <= 160


# ---------------------------------------------------------------------------
# Blocking acquire + cancel
# ---------------------------------------------------------------------------


def test_acquire_returns_true_when_tokens_available():
    b = TokenBucket(10000)  # real clock, plenty of burst
    assert b.acquire() is True


def test_acquire_honours_cancel():
    clk = _FakeClock()  # frozen clock → never refills
    b = TokenBucket(200, clock=clk)
    while b.try_acquire():
        pass  # drain
    cancel = threading.Event()
    cancel.set()
    # Empty bucket + frozen clock would block forever; cancel breaks out.
    assert b.acquire(cancel=cancel, poll=0.001) is False


def test_acquire_clamps_n_to_capacity():
    # Asking for more than capacity must not deadlock — n is clamped.
    b = TokenBucket(200)  # capacity 20, starts full
    assert b.acquire(1000) is True
