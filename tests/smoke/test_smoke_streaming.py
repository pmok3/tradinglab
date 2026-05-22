"""Per-feature smoke subset: streaming / fetch executor / scheduler / cache."""
from __future__ import annotations

import pytest

from tests.smoke.test_smoke_full import (
    check_70_fetch_executor,
    check_80_next_bar_scheduler,
    check_90_streaming_dispatch,
    check_90b_stream_refresh,
    check_95_stream_queue_coalescing,
    check_d7_poll_tick_slides_view_forward,
    check_d8_scheduler_aligns_to_bar_close,
    check_d9_poll_retry_when_bar_not_ready,
    check_d10_poll_tick_offloads_fetch_to_executor,
    check_d24_n7_async_load_offloads_to_executor,
    check_d40_smoke_cache_isolation,
    check_d47_cache_stale_session_aware,
    check_e0_disk_cache_persist,
)

_CHECKS = [
    check_70_fetch_executor,
    check_80_next_bar_scheduler,
    check_90_streaming_dispatch,
    check_90b_stream_refresh,
    check_95_stream_queue_coalescing,
    check_d7_poll_tick_slides_view_forward,
    check_d8_scheduler_aligns_to_bar_close,
    check_d9_poll_retry_when_bar_not_ready,
    check_d10_poll_tick_offloads_fetch_to_executor,
    check_d24_n7_async_load_offloads_to_executor,
    check_d40_smoke_cache_isolation,
    check_d47_cache_stale_session_aware,
    check_e0_disk_cache_persist,
]


@pytest.mark.parametrize("check", _CHECKS, ids=lambda c: c.__name__)
def test_streaming(app, check) -> None:
    check(app)
