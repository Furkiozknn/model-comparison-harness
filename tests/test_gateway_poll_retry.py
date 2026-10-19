"""GatewayBackend retries a transient poll failure a bounded number of times
instead of recording the whole backend as failed."""

from __future__ import annotations

import httpx
import pytest

from model_comparison_harness import backends as backends_module
from model_comparison_harness.backends import BackendError, GatewayBackend


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Keep the real retry count, drop the real waits."""
    count = len(backends_module.poll_retry_delays())
    monkeypatch.setattr(backends_module, "poll_retry_delays", lambda: [0.0] * count)
    return count


def _backend(poll_answers, **kwargs):
    gets = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "polling_url": "/v1/jobs/j1"})
        index = gets["n"]
        gets["n"] += 1
        if index < len(poll_answers):
            item = poll_answers[index]
            if isinstance(item, Exception):
                raise item
            return item
        return httpx.Response(200, json={"status": "ready", "result": {"ok": True}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs.setdefault("poll_interval", 0)
    backend = GatewayBackend("g", url="http://gw.test", capability="mock-generate", http_client=client, **kwargs)
    return backend, gets


async def test_a_dropped_connection_and_a_503_are_retried():
    backend, gets = _backend([httpx.ReadError("reset"), httpx.Response(503, text="busy")])
    assert await backend.run({}) == {"ok": True}
    assert gets["n"] == 3


async def test_retries_are_bounded_then_the_failure_is_reported(_fast_retries):
    backend, gets = _backend([httpx.Response(502, text="bad gateway")] * (_fast_retries + 1))
    with pytest.raises(BackendError, match=r"poll failed \(502\): bad gateway"):
        await backend.run({})
    assert gets["n"] == _fast_retries + 1


async def test_a_persistent_network_error_still_surfaces(_fast_retries):
    backend, gets = _backend([httpx.ConnectError("refused")] * (_fast_retries + 1))
    with pytest.raises(httpx.ConnectError):
        await backend.run({})
    assert gets["n"] == _fast_retries + 1


@pytest.mark.parametrize("status", [500, 404])
async def test_non_transient_poll_errors_are_not_retried(status):
    backend, gets = _backend([httpx.Response(status, text="no")])
    with pytest.raises(BackendError, match=f"poll failed \\({status}\\)"):
        await backend.run({})
    assert gets["n"] == 1


async def test_no_retry_waits_past_the_backend_timeout(monkeypatch):
    monkeypatch.setattr(backends_module, "poll_retry_delays", lambda: [30.0])
    backend, gets = _backend([httpx.Response(503, text="busy")], timeout=1.0)
    with pytest.raises(BackendError, match=r"poll failed \(503\)"):
        await backend.run({})
    assert gets["n"] == 1
