from __future__ import annotations

import pytest

from model_comparison_harness.backends import MockBackend
from model_comparison_harness.runner import run_comparison


@pytest.mark.asyncio
async def test_run_comparison_empty_backends_returns_empty_list():
    assert await run_comparison([], {}) == []


@pytest.mark.asyncio
async def test_run_comparison_preserves_backend_order_in_results():
    backends = [
        MockBackend("a", delay_seconds=0.05),
        MockBackend("b", delay_seconds=0.01),
        MockBackend("c", delay_seconds=0.03),
    ]
    results = await run_comparison(backends, {})
    assert [r.backend for r in results] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_run_comparison_runs_concurrently_not_sequentially():
    # Three backends each sleeping 0.2s should finish in ~0.2s total if run
    # concurrently, not ~0.6s if run sequentially. Generous margin for CI jitter.
    import time

    backends = [MockBackend(f"m{i}", delay_seconds=0.2) for i in range(3)]
    start = time.monotonic()
    await run_comparison(backends, {})
    elapsed = time.monotonic() - start
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_run_comparison_success_result():
    backends = [MockBackend("m", delay_seconds=0, result={"x": 1})]
    results = await run_comparison(backends, {"prompt": "hi"})
    assert results[0].status == "success"
    assert results[0].error is None
    assert results[0].result["x"] == 1
    assert results[0].latency_seconds >= 0


@pytest.mark.asyncio
async def test_run_comparison_one_failure_does_not_affect_others():
    backends = [
        MockBackend("good", delay_seconds=0),
        MockBackend("bad", delay_seconds=0, should_fail=True, failure_message="boom"),
    ]
    results = await run_comparison(backends, {})
    good, bad = results
    assert good.status == "success"
    assert bad.status == "error"
    assert bad.error == "boom"
    assert bad.result is None


@pytest.mark.asyncio
async def test_run_comparison_failure_carries_error_type():
    backends = [MockBackend("bad", delay_seconds=0, should_fail=True, failure_message="boom")]
    results = await run_comparison(backends, {})
    assert results[0].error_type == "BackendError"


@pytest.mark.asyncio
async def test_run_comparison_success_has_no_error_type():
    backends = [MockBackend("good", delay_seconds=0)]
    results = await run_comparison(backends, {})
    assert results[0].error_type is None


@pytest.mark.asyncio
async def test_run_comparison_timeout_reports_error_without_blocking_others():
    # A backend slower than the harness `timeout` is reported as a timeout
    # error - and, critically, a fast sibling backend's own result is
    # unaffected (concurrency + independent failure isolation still hold).
    backends = [
        MockBackend("slow", delay_seconds=0.3),
        MockBackend("fast", delay_seconds=0),
    ]
    results = await run_comparison(backends, {}, timeout=0.05)
    slow, fast = results
    assert slow.status == "error"
    assert slow.error_type == "TimeoutError"
    assert "0.05" in slow.error
    assert fast.status == "success"


@pytest.mark.asyncio
async def test_run_comparison_no_timeout_by_default_lets_slow_backend_finish():
    backends = [MockBackend("slow", delay_seconds=0.05)]
    results = await run_comparison(backends, {})
    assert results[0].status == "success"
