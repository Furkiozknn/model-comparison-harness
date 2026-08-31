from __future__ import annotations

import httpx
import pytest

from model_comparison_harness.backends import BackendError, GatewayBackend, HttpBackend, MockBackend


@pytest.mark.asyncio
async def test_mock_backend_returns_configured_result_plus_params():
    backend = MockBackend("m", delay_seconds=0, result={"x": 1})
    result = await backend.run({"prompt": "hi"})
    assert result == {"x": 1, "params_received": {"prompt": "hi"}}


@pytest.mark.asyncio
async def test_mock_backend_default_result_when_none_given():
    backend = MockBackend("m", delay_seconds=0)
    result = await backend.run({})
    assert result["note"] == "mock result"


@pytest.mark.asyncio
async def test_mock_backend_raises_when_configured_to_fail():
    backend = MockBackend("m", delay_seconds=0, should_fail=True, failure_message="nope")
    with pytest.raises(BackendError, match="nope"):
        await backend.run({})


@pytest.mark.asyncio
async def test_http_backend_posts_and_returns_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/generate"
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HttpBackend("h", url="http://api.test/generate", http_client=client)
    result = await backend.run({"prompt": "hi"})
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_http_backend_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HttpBackend("h", url="http://api.test/generate", http_client=client)
    with pytest.raises(BackendError, match="500"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_submits_polls_and_returns_result():
    calls = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == "/v1/mock-generate"
            return httpx.Response(202, json={"id": "j1", "polling_url": "/v1/jobs/j1"})
        calls["polls"] += 1
        if calls["polls"] < 2:
            return httpx.Response(200, json={"status": "processing"})
        return httpx.Response(200, json={"status": "ready", "result": {"done": True}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = GatewayBackend(
        "g", url="http://gw.test", capability="mock-generate", poll_interval=0, http_client=client
    )
    result = await backend.run({"prompt": "hi"})
    assert result == {"done": True}


@pytest.mark.asyncio
async def test_gateway_backend_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "polling_url": "/v1/jobs/j1"})
        return httpx.Response(200, json={"status": "error", "error": "provider exploded"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = GatewayBackend(
        "g", url="http://gw.test", capability="mock-generate", poll_interval=0, http_client=client
    )
    with pytest.raises(BackendError, match="provider exploded"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_raises_on_submission_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="unknown capability")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = GatewayBackend("g", url="http://gw.test", capability="nope", http_client=client)
    with pytest.raises(BackendError, match="404"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_raises_on_expired_status():
    """The gateway returns 410 Gone (not a 200 body with status='expired')
    once a terminal job's result has passed its TTL -- this must surface as
    a clean BackendError, not an unhandled httpx.HTTPStatusError."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "polling_url": "/v1/jobs/j1"})
        return httpx.Response(410, json={"detail": "this job's result has expired"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = GatewayBackend(
        "g", url="http://gw.test", capability="mock-generate", poll_interval=0, http_client=client
    )
    with pytest.raises(BackendError, match="expired"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_times_out_if_never_ready():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "polling_url": "/v1/jobs/j1"})
        return httpx.Response(200, json={"status": "processing"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = GatewayBackend(
        "g", url="http://gw.test", capability="mock-generate", timeout=0.05, poll_interval=0.01, http_client=client
    )
    with pytest.raises(BackendError, match="did not finish within"):
        await backend.run({})
