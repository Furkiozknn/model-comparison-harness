"""Command-line entry point: `mch run|validate`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from typing import Any

from .config import ConfigError, load_backends_from_file
from .runner import ComparisonResult, run_comparison


def _format_table(results: list[ComparisonResult]) -> str:
    headers = ["backend", "status", "latency (s)", "summary"]
    rows = []
    for r in results:
        if r.status == "success":
            summary = json.dumps(r.result)
        else:
            summary = f"ERROR: {r.error}"
        if len(summary) > 80:
            summary = summary[:77] + "..."
        rows.append([r.backend, r.status, f"{r.latency_seconds:.3f}", summary])

    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h) for i, h in enumerate(headers)]
    lines = []
    lines.append("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))

    fastest_success = min(
        (r for r in results if r.status == "success"), key=lambda r: r.latency_seconds, default=None
    )
    if fastest_success:
        lines.append("")
        lines.append(f"fastest successful backend: {fastest_success.backend} ({fastest_success.latency_seconds:.3f}s)")

    n_success = sum(1 for r in results if r.status == "success")
    n_error = len(results) - n_success
    lines.append(f"{n_success} succeeded, {n_error} failed")
    return "\n".join(lines)


def _cmd_validate(args: argparse.Namespace) -> None:
    try:
        backends = load_backends_from_file(args.config)
    except ConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"OK: {len(backends)} backend(s) configured: {', '.join(b.name for b in backends)}")


def _cmd_run(args: argparse.Namespace) -> None:
    try:
        backends = load_backends_from_file(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    try:
        params: dict[str, Any] = json.loads(args.input)
    except json.JSONDecodeError as exc:
        print(f"error: --input must be valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(params, dict):
        print("error: --input must be a JSON object", file=sys.stderr)
        raise SystemExit(1)

    results = asyncio.run(run_comparison(backends, params))

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(_format_table(results))

    if args.fail_on_error and any(r.status == "error" for r in results):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="mch", description="model-comparison-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="check a comparison config for structural problems")
    validate_parser.add_argument("config")
    validate_parser.set_defaults(func=_cmd_validate)

    run_parser = subparsers.add_parser(
        "run", help="run the same input against every configured backend concurrently"
    )
    run_parser.add_argument("config")
    run_parser.add_argument("--input", required=True, help="JSON object, e.g. '{\"prompt\": \"a cat\"}'")
    run_parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of a table")
    run_parser.add_argument(
        "--fail-on-error", action="store_true", help="exit non-zero if any backend errored"
    )
    run_parser.set_defaults(func=_cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
