"""Load a comparison config (YAML) into a list of Backend instances."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .backends import Backend, GatewayBackend, HttpBackend, MockBackend


class ConfigError(Exception):
    """Raised for a malformed config: unknown backend type, missing
    required field for that type, duplicate backend names, etc."""


def _build_mock(name: str, spec: dict[str, Any]) -> MockBackend:
    return MockBackend(
        name,
        delay_seconds=spec.get("delay", 0.05),
        result=spec.get("result"),
        should_fail=spec.get("should_fail", False),
        failure_message=spec.get("failure_message", "mock backend was configured to fail"),
    )


def _build_gateway(name: str, spec: dict[str, Any]) -> GatewayBackend:
    for field in ("url", "capability"):
        if field not in spec:
            raise ConfigError(f"backend {name!r} (type=gateway): missing required field {field!r}")
    return GatewayBackend(
        name,
        url=spec["url"],
        capability=spec["capability"],
        timeout=spec.get("timeout", 60.0),
        poll_interval=spec.get("poll_interval", 0.3),
    )


def _build_http(name: str, spec: dict[str, Any]) -> HttpBackend:
    if "url" not in spec:
        raise ConfigError(f"backend {name!r} (type=http): missing required field 'url'")
    return HttpBackend(
        name,
        url=spec["url"],
        headers=spec.get("headers"),
        timeout=spec.get("timeout", 60.0),
    )


_BUILDERS = {
    "mock": _build_mock,
    "gateway": _build_gateway,
    "http": _build_http,
}


def load_backends_from_dict(data: dict[str, Any]) -> list[Backend]:
    raw_backends = data.get("backends")
    if not raw_backends or not isinstance(raw_backends, list):
        raise ConfigError("config must have a non-empty 'backends' list")

    backends: list[Backend] = []
    seen_names: set[str] = set()
    for i, spec in enumerate(raw_backends):
        if not isinstance(spec, dict):
            raise ConfigError(f"backends[{i}] must be a mapping, got {type(spec).__name__}")
        name = spec.get("name")
        if not name:
            raise ConfigError(f"backends[{i}] is missing a 'name'")
        if name in seen_names:
            raise ConfigError(f"duplicate backend name: {name!r}")
        seen_names.add(name)

        backend_type = spec.get("type")
        builder = _BUILDERS.get(backend_type)
        if builder is None:
            raise ConfigError(
                f"backend {name!r}: unknown type {backend_type!r}, must be one of {sorted(_BUILDERS)}"
            )
        backends.append(builder(name, spec))

    return backends


def load_backends_from_file(path: str | Path) -> list[Backend]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"no such file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a YAML mapping at the top level")
    return load_backends_from_dict(data)
