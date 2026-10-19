"""Summaries over repeated runs: success rate and latency percentiles.

One run per backend answers "which one answered, and roughly how fast",
and a single latency figure is noise: a cold connection, a GC pause, a
provider's queue. ``mch run --repeat N`` runs the same comparison N times,
and this module turns those rows into one line per backend.

Latency statistics are computed over **successful** runs only. A timeout's
latency is the timeout itself and an instant connection refusal is near
zero; folding either into p50 would describe the failure, not the backend.
Failures are counted instead, by ``error_type``.

Percentiles use linear interpolation between closest ranks (the default
of NumPy's ``percentile`` and of R's type 7), so they are defined for any
number of samples. With few runs p95 is close to the maximum; that is the
honest reading of five samples, not a precise tail estimate.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .runner import ComparisonResult


def percentile(values: Sequence[float], q: float) -> float:
    """The ``q``-th percentile (0-100) of ``values`` by linear interpolation.
    Raises ValueError for an empty sequence or ``q`` outside 0-100."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    if not 0 <= q <= 100:
        raise ValueError(f"q must be between 0 and 100, got {q}")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@dataclass
class BackendSummary:
    """One backend across every round of a repeated comparison."""

    backend: str
    runs: int
    successes: int
    success_rate: float
    #: Latency over successful runs only, in seconds; None if none succeeded.
    latency_min: Optional[float] = None
    latency_p50: Optional[float] = None
    latency_p95: Optional[float] = None
    latency_max: Optional[float] = None
    latency_mean: Optional[float] = None
    latency_stdev: Optional[float] = None
    #: Failure counts by error_type, e.g. {"TimeoutError": 2}.
    errors: dict[str, int] = field(default_factory=dict)


def summarize(rounds: Sequence[Sequence[ComparisonResult]]) -> list[BackendSummary]:
    """One :class:`BackendSummary` per backend, in the order backends first
    appear in ``rounds``."""
    order: list[str] = []
    by_backend: dict[str, list[ComparisonResult]] = {}
    for round_results in rounds:
        for result in round_results:
            if result.backend not in by_backend:
                order.append(result.backend)
                by_backend[result.backend] = []
            by_backend[result.backend].append(result)

    summaries = []
    for name in order:
        results = by_backend[name]
        latencies = [r.latency_seconds for r in results if r.status == "success"]
        errors = Counter(r.error_type or "Error" for r in results if r.status != "success")
        summary = BackendSummary(
            backend=name,
            runs=len(results),
            successes=len(latencies),
            success_rate=len(latencies) / len(results),
            errors=dict(sorted(errors.items())),
        )
        if latencies:
            summary.latency_min = min(latencies)
            summary.latency_p50 = percentile(latencies, 50)
            summary.latency_p95 = percentile(latencies, 95)
            summary.latency_max = max(latencies)
            summary.latency_mean = statistics.fmean(latencies)
            summary.latency_stdev = statistics.stdev(latencies) if len(latencies) > 1 else 0.0
        summaries.append(summary)
    return summaries
