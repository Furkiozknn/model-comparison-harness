from __future__ import annotations

import pytest

import model_comparison_harness.runner as runner_module
from model_comparison_harness.backends import MockBackend
from model_comparison_harness.grading import GradeResult, GradingUnavailable
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


# --- rubric / model-graded scoring -----------------------------------------

@pytest.mark.asyncio
async def test_no_rubric_means_no_grade_at_all():
    backends = [MockBackend("m", delay_seconds=0)]
    results = await run_comparison(backends, {})
    assert results[0].grade is None


@pytest.mark.asyncio
async def test_rubric_grades_only_successful_results(monkeypatch):
    async def fake_grade_result(output, rubric):
        return GradeResult(passed=True, score=1.0, reason="ok")

    monkeypatch.setattr(runner_module, "grade_result", fake_grade_result)

    backends = [
        MockBackend("good", delay_seconds=0),
        MockBackend("bad", delay_seconds=0, should_fail=True),
    ]
    results = await run_comparison(backends, {}, rubric="anything")

    good, bad = results
    assert good.grade == GradeResult(passed=True, score=1.0, reason="ok")
    assert bad.grade is None  # nothing to judge for a failed backend call


@pytest.mark.asyncio
async def test_grading_unavailable_is_reported_per_result_not_raised(monkeypatch):
    async def fake_grade_result(output, rubric):
        raise GradingUnavailable("no judge configured")

    monkeypatch.setattr(runner_module, "grade_result", fake_grade_result)

    backends = [MockBackend("m", delay_seconds=0)]
    results = await run_comparison(backends, {}, rubric="anything")  # must not raise

    assert results[0].grade.passed is False
    assert "no judge configured" in results[0].grade.reason


@pytest.mark.asyncio
async def test_latency_excludes_grading_time(monkeypatch):
    # latency_seconds is the metric the whole tool exists to compare - a slow
    # judge-model round-trip must never be attributed to the backend being graded.
    import asyncio

    async def slow_grade_result(output, rubric):
        await asyncio.sleep(0.3)
        return GradeResult(passed=True, score=1.0, reason="ok")

    monkeypatch.setattr(runner_module, "grade_result", slow_grade_result)

    backends = [MockBackend("m", delay_seconds=0.01)]
    results = await run_comparison(backends, {}, rubric="anything")

    assert results[0].latency_seconds < 0.1  # well under the 0.3s grading delay
    assert results[0].grade.passed is True


@pytest.mark.asyncio
async def test_unexpected_grading_error_does_not_crash_the_comparison(monkeypatch):
    async def fake_grade_result(output, rubric):
        raise RuntimeError("judge API timed out")

    monkeypatch.setattr(runner_module, "grade_result", fake_grade_result)

    backends = [MockBackend("m", delay_seconds=0)]
    results = await run_comparison(backends, {}, rubric="anything")  # must not raise

    assert results[0].status == "success"  # the backend call itself still succeeded
    assert results[0].grade.passed is False
    assert "judge API timed out" in results[0].grade.reason
