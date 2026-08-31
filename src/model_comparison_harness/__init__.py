"""model-comparison-harness: run the same request against multiple
generative-model backends concurrently, compare latency/success/results.

Public surface::

    from model_comparison_harness import (
        Backend, MockBackend, GatewayBackend, HttpBackend, BackendError,
        load_backends_from_file, load_backends_from_dict, ConfigError,
        run_comparison, ComparisonResult,
    )
"""

from __future__ import annotations

from .backends import Backend, BackendError, GatewayBackend, HttpBackend, MockBackend
from .config import ConfigError, load_backends_from_dict, load_backends_from_file
from .runner import ComparisonResult, run_comparison

__all__ = [
    "Backend",
    "BackendError",
    "GatewayBackend",
    "HttpBackend",
    "MockBackend",
    "ConfigError",
    "load_backends_from_dict",
    "load_backends_from_file",
    "ComparisonResult",
    "run_comparison",
]

__version__ = "0.1.0"
