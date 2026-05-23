"""Unit tests for strategy_tester.acceptance."""

from __future__ import annotations

import pytest

from tradinglab.strategy_tester import AcceptanceToken, RunCancelled


def test_default_state_not_cancelled() -> None:
    t = AcceptanceToken()
    assert t.is_cancelled() is False


def test_cancel_idempotent() -> None:
    t = AcceptanceToken()
    t.cancel()
    t.cancel()  # idempotent
    assert t.is_cancelled() is True


def test_raise_if_cancelled() -> None:
    t = AcceptanceToken()
    t.raise_if_cancelled()  # no-op when not cancelled
    t.cancel()
    with pytest.raises(RunCancelled):
        t.raise_if_cancelled()


def test_token_is_thread_independent() -> None:
    """Two tokens are independent."""
    a = AcceptanceToken()
    b = AcceptanceToken()
    a.cancel()
    assert a.is_cancelled()
    assert not b.is_cancelled()
