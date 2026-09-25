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


# Every response body (an `http` backend's, and each gateway submission/poll
# response) is read into memory in full, so a misbehaving or hostile endpoint
# could otherwise stream an unbounded body at us for as long as the timeout
# allows. 10 MiB is far above any JSON a model API returns for one request;
# raise it per backend with `max_response_bytes`.
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


async def _read_capped(response: httpx.Response, limit: int) -> bytes:
    """Read a streamed response body, refusing to hold more than `limit`
    bytes. Counts decoded bytes, so a small gzip body that inflates past the
    limit is caught too."""
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


async def _send_capped(
    client: httpx.AsyncClient, method: str, url: str, *, limit: int, **kwargs: Any
) -> tuple[httpx.Response, bytes]:
    async with client.stream(method, url, **kwargs) as response:
        body = await _read_capped(response, limit)
    return response, body


def _decode(response: httpx.Response, body: bytes) -> str:
    return body.decode(response.encoding or "utf-8", errors="replace")


def _json_body(response: httpx.Response, body: bytes, what: str) -> Any:
    try:
        return json.loads(body)
    except ValueError as exc:
        raise BackendError(
            f"{what} was not JSON ({response.status_code}, "
            f"content-type {response.headers.get('content-type', '?')!r}): {_clip(_decode(response, body))}"
        ) from exc


def _json_object(response: httpx.Response, body: bytes, what: str) -> dict[str, Any]:
    data = _json_body(response, body, what)
    if not isinstance(data, dict):
        raise BackendError(f"{what} was not a JSON object: {_clip(json.dumps(data))}")
    return data


class GatewayBackend(Backend):
    """Talks to an ai-job-gateway-compatible server: POST /v1/{capability},
    poll the returned polling_url until ready/error/expired.

    Works against any server implementing that same submit/poll contract,
    not only the `ai-job-gateway` repo specifically.

    The response-interpretation rules live in the vendored `gateway_poll`
    module, which must stay byte-identical to ai-job-gateway's copy (CI checks
    it). Everything that protects *this* process from the server - the body
    size cap, the JSON/shape checks, keeping polls on the configured origin,
    the wall-clock ceiling - is therefore done here, around those calls.
    """

    def __init__(
        self,
        name: str,
        *,
        url: str,
        capability: str,
        timeout: float = 60.0,
        poll_interval: float = 0.3,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.name = name
        self.base_url = url.rstrip("/")
        self.capability = capability
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_response_bytes = max_response_bytes
        self._http_client = http_client
        self._owns_client = http_client is None

    def _poll_url(self, polling_url: Any) -> str:
        """The gateway chooses polling_url, and resolve_polling_url simply
        appends it to the base URL. A value such as "@other-host/x" would turn
        "http://gw" into "http://gw@other-host/x" - a request to a host the
        config never named. Only a path on the configured origin is accepted."""
        if not isinstance(polling_url, str) or not polling_url.startswith("/"):
            raise BackendError(f"gateway returned an invalid polling_url {polling_url!r} (expected a path starting with '/')")
        full = resolve_polling_url(self.base_url, polling_url)
        base, resolved = httpx.URL(self.base_url), httpx.URL(full)
        if (resolved.scheme, resolved.host, resolved.port) != (base.scheme, base.host, base.port):
            raise BackendError(f"gateway returned a polling_url that leaves {self.base_url}: {polling_url!r}")
        return full

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        last_status: list[Any] = [None]
        try:
            # The deadline below is only checked between polls; this ceiling
            # also covers a submission or poll response that trickles in
            # slowly enough to never trip httpx's per-read timeout.
            async with asyncio.timeout(self.timeout):
                return await self._run(params, last_status)
        except TimeoutError:
            raise BackendError(
                f"did not finish within {self.timeout}s (last status: {last_status[0]!r})"
            ) from None

    async def _run(self, params: dict[str, Any], last_status: list[Any]) -> Any:
        client = self._http_client or httpx.AsyncClient()
        limit = self.max_response_bytes
        deadline = time.monotonic() + self.timeout
        try:
            response, body = await _send_capped(
                client, "POST", submit_url(self.base_url, self.capability), limit=limit,
                json=params, timeout=self.timeout,
            )
            body_json = (
                _json_object(response, body, "submission response") if response.status_code < 400 else None
            )
            try:
                _job_id, polling_url = parse_submission(response.status_code, body_json, _decode(response, body))
            except GatewayHTTPError as exc:
                raise BackendError(f"submission rejected ({exc.status_code}): {_clip(exc.body_text)}") from exc
            except KeyError as exc:
                raise BackendError(f"submission response is missing {exc.args[0]!r}: {_clip(json.dumps(body_json))}") from exc
            poll_url = self._poll_url(polling_url)

            while True:
                # Each poll gets only the time remaining until `deadline`, not
                # the full self.timeout again - otherwise one slow poll
                # request near the end of the window can push total wall-clock
                # time to roughly 2x the configured timeout before the
                # deadline check below ever runs.
                remaining = max(0.01, deadline - time.monotonic())
                poll_response, poll_body = await _send_capped(client, "GET", poll_url, limit=limit, timeout=remaining)
                if is_expired_poll_response(poll_response.status_code):
                    try:
                        detail_json = json.loads(poll_body)
                    except ValueError:
                        detail_json = None
                    detail = expired_detail(detail_json if isinstance(detail_json, dict) else None)
                    raise BackendError(f"job expired: {_clip(str(detail))}")
                if poll_response.status_code >= 400:
                    # Same clean BackendError shape the submission path
                    # already uses, instead of a raw httpx exception message.
                    raise BackendError(
                        f"poll failed ({poll_response.status_code}): {_clip(_decode(poll_response, poll_body))}"
                    )
                outcome = classify_poll_body(_json_object(poll_response, poll_body, "poll response"))
                last_status[0] = outcome.status
                if outcome.ready:
                    return outcome.result
                if outcome.terminal:
                    # A fixed prefix: the rest is server-chosen text, and it
                    # ends up at the start of a table/CSV cell.
                    raise BackendError(f"job failed: {_clip(str(outcome.error_message))}")
                if time.monotonic() >= deadline:
                    raise BackendError(f"did not finish within {self.timeout}s (last status: {outcome.status!r})")
                await asyncio.sleep(min(self.poll_interval, max(0.0, deadline - time.monotonic())))
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

    async def run(self, params: dict[str, Any]) -> Any:
        try:
            # httpx's `timeout` bounds each connect/read/write separately, so a
            # server that sends one byte just inside every read timeout could
            # hold a run open indefinitely. `timeout` is a total ceiling.
            async with asyncio.timeout(self.timeout):
                return await self._run(params)
        except TimeoutError:
            raise TimeoutError(f"http backend did not finish within {self.timeout}s") from None

    async def _run(self, params: dict[str, Any]) -> Any:
        # follow_redirects=False (httpx's default, stated here on purpose): a
        # redirect would re-send the request - and its headers, which may
        # carry an API key - possibly to another host, so it is reported
        # instead of followed.
        client = self._http_client or httpx.AsyncClient(follow_redirects=False)
        try:
            response, body = await _send_capped(
                client, "POST", self.url, limit=self.max_response_bytes,
                json=params, headers=self.headers, timeout=self.timeout,
            )
            if response.is_redirect:
                location = response.headers.get("location", "?")
                raise BackendError(
                    f"request was redirected ({response.status_code} -> {_clip(location)}); "
                    "redirects are not followed - point 'url' at the final address"
                )
            if response.status_code >= 400:
                raise BackendError(f"request failed ({response.status_code}): {_clip(_decode(response, body))}")
            return _json_body(response, body, "response")
        finally:
            if self._owns_client:
                await client.aclose()
