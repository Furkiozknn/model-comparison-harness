from __future__ import annotations

import re
from pathlib import Path

import pytest

from model_comparison_harness.backends import GatewayBackend, HttpBackend, MockBackend
from model_comparison_harness.config import ConfigError, load_backends_from_dict, load_backends_from_file


def test_load_mock_backend():
    backends = load_backends_from_dict(
        {"backends": [{"name": "m", "type": "mock", "delay": 0.1, "result": {"a": 1}}]}
    )
    assert len(backends) == 1
    assert isinstance(backends[0], MockBackend)
    assert backends[0].name == "m"
    assert backends[0].delay_seconds == 0.1


def test_load_gateway_backend():
    backends = load_backends_from_dict(
        {"backends": [{"name": "g", "type": "gateway", "url": "http://x", "capability": "echo"}]}
    )
    assert isinstance(backends[0], GatewayBackend)
    assert backends[0].capability == "echo"


def test_load_http_backend():
    backends = load_backends_from_dict({"backends": [{"name": "h", "type": "http", "url": "http://x/generate"}]})
    assert isinstance(backends[0], HttpBackend)
    assert backends[0].url == "http://x/generate"


def test_load_multiple_backends_preserves_order():
    backends = load_backends_from_dict(
        {
            "backends": [
                {"name": "a", "type": "mock"},
                {"name": "b", "type": "mock"},
                {"name": "c", "type": "mock"},
            ]
        }
    )
    assert [b.name for b in backends] == ["a", "b", "c"]


def test_missing_backends_key_raises():
    with pytest.raises(ConfigError, match="non-empty 'backends' list"):
        load_backends_from_dict({})


def test_empty_backends_list_raises():
    with pytest.raises(ConfigError, match="non-empty 'backends' list"):
        load_backends_from_dict({"backends": []})


def test_backend_missing_name_raises():
    with pytest.raises(ConfigError, match="missing a 'name'"):
        load_backends_from_dict({"backends": [{"type": "mock"}]})


def test_duplicate_backend_names_raises():
    with pytest.raises(ConfigError, match="duplicate backend name"):
        load_backends_from_dict({"backends": [{"name": "a", "type": "mock"}, {"name": "a", "type": "mock"}]})


def test_unknown_backend_type_raises():
    with pytest.raises(ConfigError, match="unknown type"):
        load_backends_from_dict({"backends": [{"name": "a", "type": "not-a-real-type"}]})


def test_gateway_backend_missing_required_field_raises():
    with pytest.raises(ConfigError, match="missing required field 'capability'"):
        load_backends_from_dict({"backends": [{"name": "g", "type": "gateway", "url": "http://x"}]})


@pytest.mark.parametrize("capability", [
    "../admin/delete-all",     # escapes the /v1/ namespace via dot-segments
    "images?admin=true",       # injects query parameters
    "images#frag",             # truncates the path
    "images/extra",            # a second path segment
    "im ages",                 # whitespace in a URL path
    "images\n",                # `$` alone would let this through
    "",
    123,
])
def test_gateway_capability_that_would_rewrite_the_url_is_refused(capability):
    """`capability` is interpolated straight into f"{base_url}/v1/{capability}".

    Anything but a single plain path segment is a path- or query-injection
    into the submission request, which is why the sibling ai-workflow-engine
    guards the same input with the same shape.
    """
    with pytest.raises(ConfigError, match="must match"):
        load_backends_from_dict(
            {"backends": [
                {"name": "g", "type": "gateway", "url": "http://x", "capability": capability}
            ]}
        )


@pytest.mark.parametrize("capability", ["echo", "mock-generate", "text_to_image", "v2"])
def test_a_plain_capability_still_loads(capability):
    backends = load_backends_from_dict(
        {"backends": [
            {"name": "g", "type": "gateway", "url": "http://x", "capability": capability}
        ]}
    )
    assert backends[0].capability == capability


def test_http_backend_missing_url_raises():
    with pytest.raises(ConfigError, match="missing required field 'url'"):
        load_backends_from_dict({"backends": [{"name": "h", "type": "http"}]})


def test_load_backends_from_file_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="no such file"):
        load_backends_from_file(tmp_path / "nope.yaml")


def test_load_backends_from_file_reads_yaml(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("backends:\n  - name: m\n    type: mock\n")
    backends = load_backends_from_file(path)
    assert backends[0].name == "m"


def test_shipped_example_configs_are_valid():
    repo_root = Path(__file__).resolve().parent.parent
    for example in (repo_root / "examples").glob("*.yaml"):
        backends = load_backends_from_file(example)
        assert len(backends) >= 1


# --- field-level validation -------------------------------------------------
# Each of these used to pass `mch validate` ("OK: ...") and then either crash
# `mch run` with a traceback or silently run with a default value.


@pytest.mark.parametrize(
    "spec, message",
    [
        ({"type": "mock", "dealy": 3}, "unknown field 'dealy' - did you mean 'delay'"),
        ({"type": "http", "url": "http://x", "timout": 5}, "unknown field 'timout' - did you mean 'timeout'"),
        ({"type": "mock", "delay": "fast"}, "'delay' must be a number >= 0"),
        ({"type": "mock", "delay": -1}, "'delay' must be a number >= 0"),
        ({"type": "mock", "delay": True}, "'delay' must be a number >= 0"),
        ({"type": "mock", "result": [1, 2]}, "'result' must be a mapping"),
        ({"type": "mock", "should_fail": "yes please"}, "'should_fail' must be true or false"),
        ({"type": "mock", "failure_message": 42}, "'failure_message' must be a string"),
        ({"type": "http", "url": 42}, "'url' must be a string"),
        ({"type": "http", "url": "api.test/generate"}, "absolute http:// or https:// URL"),
        ({"type": "http", "url": "file:///etc/passwd"}, "absolute http:// or https:// URL"),
        ({"type": "http", "url": "http://x", "timeout": 0}, "'timeout' must be a number > 0"),
        ({"type": "http", "url": "http://x", "timeout": -1}, "'timeout' must be a number > 0"),
        ({"type": "http", "url": "http://x", "headers": "nope"}, "'headers' must be a mapping of string to string"),
        ({"type": "http", "url": "http://x", "headers": {"X-Retries": 3}}, "'headers' must be a mapping"),
        ({"type": "http", "url": "http://x", "max_response_bytes": 0}, "'max_response_bytes' must be a number > 0"),
        (
            {"type": "gateway", "url": "http://x", "capability": "echo", "poll_interval": 0},
            "'poll_interval' must be a number > 0",
        ),
    ],
)
def test_invalid_field_values_are_rejected_at_load_time(spec, message):
    with pytest.raises(ConfigError, match=re.escape(message)):
        load_backends_from_dict({"backends": [{"name": "b", **spec}]})


def test_non_string_name_is_rejected():
    with pytest.raises(ConfigError, match="'name' must be a string"):
        load_backends_from_dict({"backends": [{"name": 5, "type": "mock"}]})


def test_non_string_type_is_reported_as_unknown_type_not_a_crash():
    with pytest.raises(ConfigError, match="unknown type"):
        load_backends_from_dict({"backends": [{"name": "b", "type": ["mock"]}]})


def test_valid_optional_fields_still_load():
    backends = load_backends_from_dict(
        {
            "backends": [
                {"name": "m", "type": "mock", "delay": 0, "should_fail": False, "failure_message": "x"},
                {
                    "name": "h",
                    "type": "http",
                    "url": "https://api.test/v1",
                    "headers": {"Authorization": "Bearer t"},
                    "timeout": 5,
                    "max_response_bytes": 1024,
                },
                {"name": "g", "type": "gateway", "url": "http://x", "capability": "echo", "poll_interval": 0.1},
            ]
        }
    )
    assert backends[1].max_response_bytes == 1024
    assert backends[1].timeout == 5.0


def test_load_backends_from_file_directory_is_a_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="not a file"):
        load_backends_from_file(tmp_path)


def test_load_backends_from_file_non_utf8_is_a_config_error(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigError, match="not UTF-8"):
        load_backends_from_file(path)
