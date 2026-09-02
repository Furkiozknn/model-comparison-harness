"""Runs the same params against every backend concurrently and collects
timing + success/failure + result for each - the actual comparison."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from .backends import Backend


@dataclass
class ComparisonResult:
    backend: str
    status: str  # "success" | "error"
    latency_seconds: float
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    error_type: Optional[str] = None  # exception class name, e.g. "TimeoutError",
    # "BackendError" - lets scripts branch on failure *kind* without
    # string-matching `error`.


async def _run_one(backend: Backend, params: dict[str, Any], timeout: Optional[float]) -> ComparisonResult:
    start = time.monotonic()
    try:
        if timeout is not None:
            # A hard, harness-enforced ceiling on any single backend's
            # run() call. This exists independently of whatever timeout
            # (if any) a backend implementation honors internally - a
            # buggy or slow custom backend that never returns would
            # otherwise hang the entire comparison forever, silently
            # hiding every other backend's result behind it.
            result = await asyncio.wait_for(backend.run(params), timeout=timeout)
        else:
            result = await backend.run(params)
    except asyncio.TimeoutError:
        return ComparisonResult(
            backend=backend.name,
            status="error",
            latency_seconds=time.monotonic() - start,
            error=f"backend did not respond within {timeout}s (harness timeout)",
            error_type="TimeoutError",
        )
    except Exception as exc:  # noqa: BLE001 - any backend failure becomes a
        # reported error row, never an exception that kills the whole
        # comparison (one broken backend shouldn't hide every other result).
        return ComparisonResult(
            backend=backend.name,
            status="error",
            latency_seconds=time.monotonic() - start,
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return ComparisonResult(
        backend=backend.name,
        status="success",
        latency_seconds=time.monotonic() - start,
        result=result,
    )


async def run_comparison(
    backends: list[Backend], params: dict[str, Any], timeout: Optional[float] = None
) -> list[ComparisonResult]:
    """Run `params` against every backend concurrently (asyncio.gather - not
    sequentially, which would make latency numbers meaningless for
    comparison) and return one ComparisonResult per backend, in the same
    order the backends were given.

    `timeout`, if given, bounds each individual backend's run() call in
    seconds; a backend that exceeds it is reported as an error result
    (status="error", error_type="TimeoutError") rather than blocking the
    rest of the comparison indefinitely.
    """
    if not backends:
        return []
    return list(await asyncio.gather(*(_run_one(b, params, timeout) for b in backends)))
