"""Command-line entry point: `mch run|validate`."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
from dataclasses import asdict, fields
from typing import Any

from .config import ConfigError, load_backends_from_file
from .runner import ComparisonResult, run_comparison


def _format_csv(results: list[ComparisonResult]) -> str:
    """CSV rows, one per backend - for spreadsheets and shell pipelines
    (`mch run ... --csv > results.csv`, then `csv.DictReader`/pandas/etc.)."""
    buf = io.StringIO()
    fieldnames = [f.name for f in fields(ComparisonResult)]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        row = asdict(r)
        # `result` is a nested dict/None - serialize it to a JSON string so
        # it round-trips through a single CSV cell intact.
        row["result"] = json.dumps(row["result"]) if row["result"] is not None else ""
        writer.writerow(row)
    return buf.getvalue().rstrip("\n")


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

    results = asyncio.run(run_comparison(backends, params, timeout=args.timeout))

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    elif args.csv:
        print(_format_csv(results))
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
    output_format = run_parser.add_mutually_exclusive_group()
    output_format.add_argument("--json", action="store_true", help="print machine-readable JSON instead of a table")
    output_format.add_argument(
        "--csv", action="store_true", help="print CSV instead of a table (e.g. for a spreadsheet or `> file.csv`)"
    )
    run_parser.add_argument(
        "--fail-on-error", action="store_true", help="exit non-zero if any backend errored"
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "hard per-backend timeout enforced by the harness itself, on top of "
            "whatever a backend does internally - a hung or misbehaving backend is "
            "reported as a timeout error instead of blocking the whole comparison forever"
        ),
    )
    run_parser.set_defaults(func=_cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
