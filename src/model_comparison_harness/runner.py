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


async def _run_one(backend: Backend, params: dict[str, Any]) -> ComparisonResult:
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
    return ComparisonResult(
        backend=backend.name,
        status="success",
        latency_seconds=time.monotonic() - start,
        result=result,
    )


async def run_comparison(backends: list[Backend], params: dict[str, Any]) -> list[ComparisonResult]:
    """Run `params` against every backend concurrently (asyncio.gather - not
    sequentially, which would make latency numbers meaningless for
    comparison) and return one ComparisonResult per backend, in the same
    order the backends were given."""
    if not backends:
        return []
    return list(await asyncio.gather(*(_run_one(b, params) for b in backends)))
