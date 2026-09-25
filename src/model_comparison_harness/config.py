"""Load a comparison config (YAML) into a list of Backend instances."""

from __future__ import annotations

import difflib
import math
import re
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

from .backends import DEFAULT_MAX_RESPONSE_BYTES, Backend, GatewayBackend, HttpBackend, MockBackend

# gateway_poll.submit_url builds the request as f"{base_url}/v1/{capability}"
# with no encoding or escaping - an unrestricted capability string is a
# path/query injection into that request. Confirmed exploitable in the
# sibling ai-workflow-engine, which guards the same input with this same
# shape: "../admin/delete-all" escapes the /v1/ namespace entirely via
# dot-segment normalization, and "images?admin=true" injects arbitrary query
# params. Restricting to this shape closes that off before it ever reaches
# submit_url.
_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(Exception):
    """Raised for a malformed config: unknown backend type, missing
    required field for that type, duplicate backend names, etc."""


# Every field each backend type accepts. Anything else is rejected rather
# than silently ignored: a typo such as `dealy: 3` or `timout: 5` used to pass
# `mch validate` and then quietly run with the default value instead.
_COMMON_FIELDS = {"name", "type"}
_ALLOWED_FIELDS: dict[str, set[str]] = {
    "mock": _COMMON_FIELDS | {"delay", "result", "should_fail", "failure_message"},
    "gateway": _COMMON_FIELDS | {"url", "capability", "timeout", "poll_interval"},
    "http": _COMMON_FIELDS | {"url", "headers", "timeout", "max_response_bytes"},
}


def _where(name: str, spec: dict[str, Any]) -> str:
    return f"backend {name!r} (type={spec.get('type')})"


def _number(name: str, spec: dict[str, Any], field: str, default: float, *, allow_zero: bool) -> float:
    """A finite number (not a bool - YAML `yes` is a bool, and bool is an int
    subclass in Python), > 0, or >= 0 when allow_zero."""
    value = spec.get(field, default)
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if ok and (value >= 0 if allow_zero else value > 0):
        return float(value)
    bound = ">= 0" if allow_zero else "> 0"
    raise ConfigError(f"{_where(name, spec)}: {field!r} must be a number {bound}, got {value!r}")


def _string(name: str, spec: dict[str, Any], field: str, default: Optional[str] = None) -> str:
    value = spec.get(field, default)
    if not isinstance(value, str):
        raise ConfigError(f"{_where(name, spec)}: {field!r} must be a string, got {value!r}")
    return value


def _url(name: str, spec: dict[str, Any]) -> str:
    url = _string(name, spec, "url")
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        parsed = None
    if parsed is None or parsed.scheme not in ("http", "https") or not parsed.host:
        raise ConfigError(f"{_where(name, spec)}: 'url' must be an absolute http:// or https:// URL, got {url!r}")
    return url


def _require(name: str, spec: dict[str, Any], *required: str) -> None:
    for field in required:
        if field not in spec:
            raise ConfigError(f"{_where(name, spec)}: missing required field {field!r}")


def _build_mock(name: str, spec: dict[str, Any]) -> MockBackend:
    result = spec.get("result")
    if result is not None and not isinstance(result, dict):
        raise ConfigError(f"{_where(name, spec)}: 'result' must be a mapping, got {type(result).__name__}")
    should_fail = spec.get("should_fail", False)
    if not isinstance(should_fail, bool):
        raise ConfigError(f"{_where(name, spec)}: 'should_fail' must be true or false, got {should_fail!r}")
    return MockBackend(
        name,
        delay_seconds=_number(name, spec, "delay", 0.05, allow_zero=True),
        result=result,
        should_fail=should_fail,
        failure_message=_string(name, spec, "failure_message", "mock backend was configured to fail"),
    )


def _build_gateway(name: str, spec: dict[str, Any]) -> GatewayBackend:
    _require(name, spec, "url", "capability")
    capability = spec["capability"]
    # fullmatch, not match: in Python `$` also matches just before a trailing
    # newline, which would let "images\n" through into the URL path.
    if not isinstance(capability, str) or not _CAPABILITY_RE.fullmatch(capability):
        raise ConfigError(
            f"backend {name!r} (type=gateway): 'capability' {capability!r} must match "
            f"{_CAPABILITY_RE.pattern} (it becomes a URL path segment - no '/', '?', "
            "'.', or whitespace allowed)"
        )
    return GatewayBackend(
        name,
        url=_url(name, spec),
        capability=capability,
        timeout=_number(name, spec, "timeout", 60.0, allow_zero=False),
        poll_interval=_number(name, spec, "poll_interval", 0.3, allow_zero=False),
    )


def _build_http(name: str, spec: dict[str, Any]) -> HttpBackend:
    _require(name, spec, "url")
    headers = spec.get("headers")
    if headers is not None and (
        not isinstance(headers, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
    ):
        raise ConfigError(
            f"{_where(name, spec)}: 'headers' must be a mapping of string to string "
            "(quote numbers, e.g. X-Retries: \"3\")"
        )
    max_bytes = _number(name, spec, "max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES, allow_zero=False)
    return HttpBackend(
        name,
        url=_url(name, spec),
        headers=headers,
        timeout=_number(name, spec, "timeout", 60.0, allow_zero=False),
        max_response_bytes=int(max_bytes),
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
        if not isinstance(name, str):
            raise ConfigError(f"backends[{i}]: 'name' must be a string, got {name!r} (quote it in YAML)")
        if name in seen_names:
            raise ConfigError(f"duplicate backend name: {name!r}")
        seen_names.add(name)

        backend_type = spec.get("type")
        builder = _BUILDERS.get(backend_type) if isinstance(backend_type, str) else None
        if builder is None:
            raise ConfigError(
                f"backend {name!r}: unknown type {backend_type!r}, must be one of {sorted(_BUILDERS)}"
            )
        unknown = sorted(set(spec) - _ALLOWED_FIELDS[backend_type])
        if unknown:
            field = unknown[0]
            close = difflib.get_close_matches(field, sorted(_ALLOWED_FIELDS[backend_type]), n=1)
            hint = f" - did you mean {close[0]!r}?" if close else ""
            raise ConfigError(
                f"backend {name!r} (type={backend_type}): unknown field {field!r}{hint} "
                f"(allowed: {', '.join(sorted(_ALLOWED_FIELDS[backend_type]))})"
            )
        backends.append(builder(name, spec))

    return backends


def load_backends_from_file(path: str | Path) -> list[Backend]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"no such file: {path}")
    if not path.is_file():
        raise ConfigError(f"not a file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path} is not UTF-8 text: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a YAML mapping at the top level")
    return load_backends_from_dict(data)
