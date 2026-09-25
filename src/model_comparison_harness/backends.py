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
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

from .gateway_poll import (
    GatewayHTTPError,
    classify_poll_body,
    expired_detail,
    is_expired_poll_response,
    parse_submission,
    resolve_polling_url,
    submit_url,
)


# An `http` backend's response body is read into memory in full, so a
# misbehaving or hostile endpoint could otherwise stream an unbounded body at
# us for as long as the timeout allows. 10 MiB is far above any JSON a model
# API returns for one request; raise it per backend with `max_response_bytes`.
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024

# Error bodies are quoted into `error`, which lands in the table, the JSON and
# the CSV. An HTML error page or a stack trace should not become a 50 KB cell.
_MAX_ERROR_BODY_CHARS = 500


def _clip(text: str) -> str:
    if len(text) <= _MAX_ERROR_BODY_CHARS:
        return text
    return text[:_MAX_ERROR_BODY_CHARS] + f"... [{len(text) - _MAX_ERROR_BODY_CHARS} more chars]"


class BackendError(Exception):
    """Raised by a backend's run() to report a failure. You don't have to
    raise this specific type - run() can raise anything and the runner
    will catch it and record str(exc) as the error - but it's a clear,
    unambiguous choice for backends that want to be explicit."""


class Backend(ABC):
    name: str = ""

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
            response = await client.post(
                submit_url(self.base_url, self.capability), json=params, timeout=self.timeout
            )
            body_json = response.json() if response.status_code < 400 else None
            try:
                _job_id, polling_url = parse_submission(response.status_code, body_json, response.text)
            except GatewayHTTPError as exc:
                raise BackendError(f"submission rejected ({exc.status_code}): {_clip(exc.body_text)}") from exc

            deadline = time.monotonic() + self.timeout
            while True:
                # Each poll gets only the time remaining until `deadline`, not
                # the full self.timeout again - otherwise one slow poll
                # request near the end of the window can push total wall-clock
                # time to roughly 2x the configured timeout before the
                # deadline check below ever runs.
                remaining = max(0.01, deadline - time.monotonic())
                poll_response = await client.get(
                    resolve_polling_url(self.base_url, polling_url), timeout=remaining
                )
                if is_expired_poll_response(poll_response.status_code):
                    raise BackendError(expired_detail(poll_response.json()))
                try:
                    poll_response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    # Same clean BackendError shape the submission path
                    # already uses, instead of a raw httpx exception message.
                    raise BackendError(
                        f"poll failed ({poll_response.status_code}): {_clip(poll_response.text)}"
                    ) from exc
                outcome = classify_poll_body(poll_response.json())
                if outcome.ready:
                    return outcome.result
                if outcome.terminal:
                    raise BackendError(outcome.error_message)
                if time.monotonic() >= deadline:
                    raise BackendError(f"did not finish within {self.timeout}s (last status: {outcome.status!r})")
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
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._http_client = http_client
        self._owns_client = http_client is None

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        # follow_redirects=False (httpx's default, stated here on purpose): a
        # redirect would re-send the request - possibly to another host - so it
        # is reported instead of followed.
        client = self._http_client or httpx.AsyncClient(follow_redirects=False)
        try:
            async with client.stream(
                "POST", self.url, json=params, headers=self.headers, timeout=self.timeout
            ) as response:
                body = await self._read_capped(response)
            text = body.decode(response.encoding or "utf-8", errors="replace")
            if response.is_redirect:
                location = response.headers.get("location", "?")
                raise BackendError(
                    f"request was redirected ({response.status_code} -> {location}); "
                    "redirects are not followed - point 'url' at the final address"
                )
            if response.status_code >= 400:
                raise BackendError(f"request failed ({response.status_code}): {_clip(text)}")
            try:
                return json.loads(body)
            except ValueError as exc:
                raise BackendError(
                    f"response was not JSON ({response.status_code}, "
                    f"content-type {response.headers.get('content-type', '?')!r}): {_clip(text)}"
                ) from exc
        finally:
            if self._owns_client:
                await client.aclose()

    async def _read_capped(self, response: httpx.Response) -> bytes:
        limit = self.max_response_bytes
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise BackendError(f"response too large: Content-Length {declared} exceeds max_response_bytes={limit}")
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                # Stop reading here; leaving the `async with` closes the
                # connection instead of draining the rest of the body.
                raise BackendError(f"response too large: exceeded max_response_bytes={limit} while reading")
            chunks.append(chunk)
        return b"".join(chunks)
