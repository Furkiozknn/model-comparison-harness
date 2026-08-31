"""The Backend interface, plus three implementations.

A backend is the pluggable seam of this whole project: one async method,
``run(params) -> dict``, or raise. Everything else (the runner, the CLI)
is generic over "some number of backends." This is the same shape as
``ai-job-gateway``'s ``Provider`` interface, deliberately duplicated here
rather than imported - these are independent repos in the same ecosystem,
coupled only through documented HTTP contracts, never through a shared
Python dependency.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx


class BackendError(Exception):
    """Raised by a backend's run() to report a failure. You don't have to
    raise this specific type - run() can raise anything and the runner
    will catch it and record str(exc) as the error - but it's a clear,
    unambiguous choice for backends that want to be explicit."""


class Backend(ABC):
    name: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    @abstractmethod
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        """Do the work. Return a JSON-serializable result, or raise."""
        raise NotImplementedError


class MockBackend(Backend):
    """A deterministic fake backend for tests, demos, and dry-running a
    comparison config's structure before wiring up real endpoints.

    Configure a fixed (or randomized) delay and either a fixed result or a
    forced failure - useful for exercising the harness's timing/reporting
    logic without depending on any real model or network access.
    """

    def __init__(
        self,
        name: str,
        *,
        delay_seconds: float = 0.05,
        result: Optional[dict[str, Any]] = None,
        should_fail: bool = False,
        failure_message: str = "mock backend was configured to fail",
    ) -> None:
        self.name = name
        self.delay_seconds = delay_seconds
        self.result = result if result is not None else {"note": "mock result"}
        self.should_fail = should_fail
        self.failure_message = failure_message

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.should_fail:
            raise BackendError(self.failure_message)
        return {**self.result, "params_received": params}


class GatewayBackend(Backend):
    """Talks to an ai-job-gateway-compatible server: POST /v1/{capability},
    poll the returned polling_url until ready/error/expired.

    Works against any server implementing that same submit/poll contract,
    not only the `ai-job-gateway` repo specifically.
    """

    def __init__(
        self,
        name: str,
        *,
        url: str,
        capability: str,
        timeout: float = 60.0,
        poll_interval: float = 0.3,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.name = name
        self.base_url = url.rstrip("/")
        self.capability = capability
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._http_client = http_client
        self._owns_client = http_client is None

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        client = self._http_client or httpx.AsyncClient()
        try:
            response = await client.post(f"{self.base_url}/v1/{self.capability}", json=params)
            if response.status_code >= 400:
                raise BackendError(f"submission rejected ({response.status_code}): {response.text}")
            polling_url = response.json()["polling_url"]

            deadline = time.monotonic() + self.timeout
            while True:
                poll_response = await client.get(self.base_url + polling_url)
                poll_response.raise_for_status()
                record = poll_response.json()
                status = record["status"]
                if status == "ready":
                    return record["result"]
                if status in ("error", "expired"):
                    raise BackendError(record.get("error") or f"job ended with status {status!r}")
                if time.monotonic() >= deadline:
                    raise BackendError(f"did not finish within {self.timeout}s (last status: {status!r})")
                await asyncio.sleep(self.poll_interval)
        finally:
            if self._owns_client:
                await client.aclose()


class HttpBackend(Backend):
    """The simplest possible real-world backend: POST params to a fixed URL,
    treat the JSON response body as the result directly - no submit/poll
    contract assumed. Fits any synchronous request/response API.
    """

    def __init__(
        self,
        name: str,
        *,
        url: str,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 60.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self._http_client = http_client
        self._owns_client = http_client is None

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        client = self._http_client or httpx.AsyncClient()
        try:
            response = await client.post(self.url, json=params, headers=self.headers, timeout=self.timeout)
            if response.status_code >= 400:
                raise BackendError(f"request failed ({response.status_code}): {response.text}")
            return response.json()
        finally:
            if self._owns_client:
                await client.aclose()
