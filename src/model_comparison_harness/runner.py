"""Runs the same params against every backend concurrently and collects
timing + success/failure + result for each - the actual comparison."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from .backends import Backend
from .grading import GradeResult, GradingUnavailable, grade_result


@dataclass
class ComparisonResult:
    backend: str
    status: str  # "success" | "error"
    latency_seconds: float
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    grade: Optional[GradeResult] = None


async def _run_one(backend: Backend, params: dict[str, Any], rubric: Optional[str]) -> ComparisonResult:
    start = time.monotonic()
    try:
        result = await backend.run(params)
    except Exception as exc:  # noqa: BLE001 - any backend failure becomes a
        # reported error row, never an exception that kills the whole
        # comparison (one broken backend shouldn't hide every other result).
        return ComparisonResult(
            backend=backend.name,
            status="error",
            latency_seconds=time.monotonic() - start,
            error=str(exc),
        )
    # Stop the clock here, before grading - latency_seconds is the metric
    # this whole tool exists to compare (the CLI's "fastest successful
    # backend" line reads it directly), and the judge-model round-trip is a
    # separate, unrelated cost that must never leak into it.
    latency_seconds = time.monotonic() - start

    grade: Optional[GradeResult] = None
    if rubric is not None:
        try:
            grade = await grade_result(result, rubric)
        except GradingUnavailable as exc:
            # Only reachable if the judge became unavailable mid-run (e.g. a
            # key was unset between comparisons) - the CLI checks
            # availability once up front so this shouldn't normally fire,
            # but a per-result failure is still reported, not raised, since
            # one backend's grading trouble shouldn't hide every other result.
            grade = GradeResult(passed=False, score=0.0, reason=f"grading unavailable: {exc}")
        except Exception as exc:  # noqa: BLE001 - same "never crash the whole
            # comparison over one tier" rule as the backend call itself.
            grade = GradeResult(passed=False, score=0.0, reason=f"grading failed: {exc}")

    return ComparisonResult(
        backend=backend.name,
        status="success",
        latency_seconds=latency_seconds,
        result=result,
        grade=grade,
    )


async def run_comparison(
    backends: list[Backend], params: dict[str, Any], *, rubric: Optional[str] = None
) -> list[ComparisonResult]:
    """Run `params` against every backend concurrently (asyncio.gather - not
    sequentially, which would make latency numbers meaningless for
    comparison) and return one ComparisonResult per backend, in the same
    order the backends were given.

    If `rubric` is given, every successful result is also graded against it
    by a judge model (see grading.py) - an llm-rubric-style model-graded
    assertion, concurrently with every backend/grading call, not one at a
    time. Failed backend calls are never graded (nothing to judge)."""
    if not backends:
        return []
    return list(await asyncio.gather(*(_run_one(b, params, rubric) for b in backends)))
