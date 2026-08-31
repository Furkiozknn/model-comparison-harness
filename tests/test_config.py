from __future__ import annotations

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
