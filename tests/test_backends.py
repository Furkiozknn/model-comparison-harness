from __future__ import annotations

import asyncio
import time

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
async def test_gateway_backend_wraps_http_error_status_during_poll_as_backend_error():
    # A genuine HTTP-level error mid-poll (proxy/gateway hiccup returning a
    # raw 500, not a job-level {"status": "error"} body) must surface as the
    # same clean BackendError shape as every other failure path here, not a
    # raw httpx.HTTPStatusError.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "polling_url": "/v1/jobs/j1"})
        return httpx.Response(500, text="upstream hiccup")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = GatewayBackend(
        "g", url="http://gw.test", capability="mock-generate", poll_interval=0, http_client=client
    )
    with pytest.raises(BackendError, match="poll failed \\(500\\)"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_poll_timeout_shrinks_toward_the_deadline_not_reset_each_time():
    # Regression: each poll request used to get the *full* self.timeout
    # again instead of the time remaining until the overall deadline, which
    # could let total wall-clock time run to ~2x the configured timeout.
    seen_timeouts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "polling_url": "/v1/jobs/j1"})
        # The timeout httpx actually applies to this request.
        seen_timeouts.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"status": "processing"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    backend = GatewayBackend(
        "g", url="http://gw.test", capability="mock-generate", timeout=0.1, poll_interval=0.02, http_client=client
    )
    with pytest.raises(BackendError, match="did not finish"):
        await backend.run({})

    assert len(seen_timeouts) >= 2
    # Every poll's timeout must be <= the configured overall timeout, and
    # they must shrink (or hold near zero) over the course of the run - never
    # jump back up to the full 0.1s on a later poll.
    assert all(t <= 0.1 + 1e-6 for t in seen_timeouts)
    assert seen_timeouts == sorted(seen_timeouts, reverse=True)


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


# --- http backend: size cap, redirects, non-JSON -----------------------------


@pytest.mark.asyncio
async def test_http_backend_rejects_body_larger_than_max_response_bytes():
    async def stream():
        for _ in range(100):
            yield b"x" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        # No Content-Length: the cap has to hold while streaming, too.
        return httpx.Response(200, content=stream())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HttpBackend("h", url="http://api.test/generate", max_response_bytes=4096, http_client=client)
    with pytest.raises(BackendError, match="exceeded max_response_bytes=4096"):
        await backend.run({})


@pytest.mark.asyncio
async def test_http_backend_rejects_declared_oversized_content_length_before_reading():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}" * 100)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HttpBackend("h", url="http://api.test/generate", max_response_bytes=10, http_client=client)
    with pytest.raises(BackendError, match="Content-Length 200 exceeds max_response_bytes=10"):
        await backend.run({})


@pytest.mark.asyncio
async def test_http_backend_reports_redirect_instead_of_following_it():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://elsewhere.test/"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HttpBackend("h", url="http://api.test/generate", http_client=client)
    with pytest.raises(BackendError, match=r"redirected \(302 -> http://elsewhere.test/\)"):
        await backend.run({})
    assert seen == ["http://api.test/generate"]


@pytest.mark.asyncio
async def test_http_backend_non_json_body_is_a_backend_error_with_context():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>", headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HttpBackend("h", url="http://api.test/generate", http_client=client)
    with pytest.raises(BackendError, match="not JSON.*text/html.*maintenance"):
        await backend.run({})


@pytest.mark.asyncio
async def test_http_backend_clips_huge_error_bodies():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="E" * 5000)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HttpBackend("h", url="http://api.test/generate", http_client=client)
    with pytest.raises(BackendError) as exc_info:
        await backend.run({})
    message = str(exc_info.value)
    assert len(message) < 700
    assert "4500 more chars" in message


# --- gateway backend: size cap, origin, malformed bodies, wall clock ---------


def _gateway(handler, **kwargs) -> GatewayBackend:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs.setdefault("poll_interval", 0)
    return GatewayBackend("g", url="http://gw.test", capability="mock-generate", http_client=client, **kwargs)


def _submitted_then(poll_response):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "polling_url": "/v1/jobs/j1"})
        return poll_response(request)

    return handler


@pytest.mark.asyncio
async def test_gateway_backend_caps_a_streamed_poll_body():
    # Regression: poll responses were read with response.json() in full, so a
    # gateway could stream an unbounded "ready" body into memory.
    async def stream():
        yield b'{"status": "ready", "result": "'
        for _ in range(100):
            yield b"A" * 1024
        yield b'"}'

    backend = _gateway(_submitted_then(lambda r: httpx.Response(200, content=stream())), max_response_bytes=4096)
    with pytest.raises(BackendError, match="exceeded max_response_bytes=4096"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_caps_the_submission_body_too():
    backend = _gateway(lambda r: httpx.Response(202, content=b"x" * 5000), max_response_bytes=1000)
    with pytest.raises(BackendError, match="Content-Length 5000 exceeds max_response_bytes=1000"):
        await backend.run({})


@pytest.mark.parametrize("polling_url", ["@evil.test/steal", ":8081/v1/jobs/j1", ".evil.test/x"])
@pytest.mark.asyncio
async def test_gateway_backend_refuses_a_polling_url_that_changes_host_or_port(polling_url):
    # Regression: resolve_polling_url appends polling_url to the base URL, so
    # "@evil.test/steal" made the next request go to http://gw.test@evil.test/.
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "polling_url": polling_url})
        return httpx.Response(200, json={"status": "ready", "result": {}})

    with pytest.raises(BackendError, match="polling_url"):
        await _gateway(handler).run({})
    assert seen == ["gw.test"]


@pytest.mark.asyncio
async def test_gateway_backend_refuses_a_non_string_polling_url():
    backend = _gateway(lambda r: httpx.Response(202, json={"id": "j1", "polling_url": 7}))
    with pytest.raises(BackendError, match="invalid polling_url 7"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_non_json_submission_is_a_backend_error():
    # Regression: surfaced as a bare JSONDecodeError("Expecting value ...").
    backend = _gateway(lambda r: httpx.Response(202, text="<html>proxy</html>"))
    with pytest.raises(BackendError, match="submission response was not JSON.*proxy"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_submission_without_id_names_the_missing_field():
    # Regression: surfaced as KeyError with the message "'id'".
    backend = _gateway(lambda r: httpx.Response(202, json={"polling_url": "/v1/jobs/j1"}))
    with pytest.raises(BackendError, match="submission response is missing 'id'"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_poll_body_that_is_not_an_object_is_a_backend_error():
    # Regression: a JSON list made classify_poll_body raise AttributeError.
    backend = _gateway(_submitted_then(lambda r: httpx.Response(200, json=["ready"])))
    with pytest.raises(BackendError, match="poll response was not a JSON object"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_expired_with_a_non_json_body_still_reports_expiry():
    backend = _gateway(_submitted_then(lambda r: httpx.Response(410, text="gone")))
    with pytest.raises(BackendError, match="job expired: job result has expired"):
        await backend.run({})


@pytest.mark.asyncio
async def test_gateway_backend_job_error_gets_a_fixed_prefix_and_is_clipped():
    # The error text is chosen by the server and lands at the start of a
    # table/CSV cell ("=HYPERLINK(...)" in a spreadsheet); it now always
    # starts with "job failed: " and is clipped like every other quoted body.
    backend = _gateway(_submitted_then(lambda r: httpx.Response(200, json={"status": "error", "error": "=1+1" + "E" * 5000})))
    with pytest.raises(BackendError) as exc_info:
        await backend.run({})
    message = str(exc_info.value)
    assert message.startswith("job failed: =1+1")
    assert len(message) < 700


@pytest.mark.asyncio
async def test_gateway_backend_timeout_is_a_wall_clock_ceiling_even_mid_response():
    # Regression: the deadline was only checked between polls, so a poll
    # body trickling in (each chunk inside httpx's per-read timeout) could
    # hold the run open far beyond `timeout`.
    async def trickle():
        yield b'{"status": '
        await asyncio.sleep(5)
        yield b'"ready", "result": {}}'

    backend = _gateway(_submitted_then(lambda r: httpx.Response(200, content=trickle())), timeout=0.2)
    start = time.monotonic()
    with pytest.raises(BackendError, match=r"did not finish within 0.2s"):
        await backend.run({})
    assert time.monotonic() - start < 1.0


@pytest.mark.asyncio
async def test_http_backend_timeout_is_a_wall_clock_ceiling_even_mid_response():
    async def trickle():
        yield b"{"
        await asyncio.sleep(5)
        yield b"}"

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=trickle())))
    backend = HttpBackend("h", url="http://api.test/generate", timeout=0.2, http_client=client)
    start = time.monotonic()
    with pytest.raises(TimeoutError, match=r"did not finish within 0.2s"):
        await backend.run({})
    assert time.monotonic() - start < 1.0
