"""Command-line entry point: `mch run|validate`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import io
import json
import math
import sys
from dataclasses import asdict, fields
from typing import Any

from . import __version__
from .config import ConfigError, load_backends_from_file
from .grading import build_judge_chain, grading_extra_installed
from .runner import ComparisonResult, run_comparison, run_repeated
from .stats import BackendSummary, summarize


def _positive_seconds(value: str) -> float:
    """argparse type for --timeout: a finite number > 0. `0`, negatives and
    `nan` used to be accepted and turned every backend into an instant
    "did not respond within 0.0s" timeout row."""
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of seconds, got {value!r}") from None
    if not (math.isfinite(seconds) and seconds > 0):
        raise argparse.ArgumentTypeError(f"must be a number greater than 0, got {value!r}")
    return seconds


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
        # `grade` is a GradeResult dataclass (or None) - same treatment, so a
        # rubric run's verdict/score/reason survives as one JSON cell instead
        # of a Python repr.
        row["grade"] = json.dumps(row["grade"]) if row["grade"] is not None else ""
        writer.writerow(row)
    return buf.getvalue().rstrip("\n")


def _printable(text: str) -> str:
    """Escape anything that is not a printable character (ESC, BEL, CR, LF,
    C1 controls, ...). Error text and judge reasons come from remote servers
    and models; printed raw, "\x1b]0;...\x07" retitles the terminal,
    "\x1b[2J" clears it, and a newline splits a row in two. Success
    summaries are json.dumps output and already escaped."""
    if text.isprintable():
        return text
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


def _format_table(results: list[ComparisonResult]) -> str:
    graded = any(r.grade is not None for r in results)
    headers = ["backend", "status", "latency (s)", "summary"]
    if graded:
        headers.append("grade")

    rows = []
    for r in results:
        if r.status == "success":
            summary = json.dumps(r.result)
        else:
            summary = f"ERROR: {r.error}"
        summary = _printable(summary)
        if len(summary) > 80:
            summary = summary[:77] + "..."
        row = [_printable(r.backend), r.status, f"{r.latency_seconds:.3f}", summary]
        if graded:
            if r.grade is None:
                row.append("-")
            else:
                grade_cell = f"{'PASS' if r.grade.passed else 'FAIL'} {r.grade.score:.2f} - {_printable(r.grade.reason)}"
                row.append(grade_cell[:60] + "..." if len(grade_cell) > 60 else grade_cell)
        rows.append(row)

    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h) for i, h in enumerate(headers)]
    lines = []
    # rstrip: padding the last column only leaves trailing spaces behind.
    lines.append("  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip())
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())

    fastest_success = min(
        (r for r in results if r.status == "success"), key=lambda r: r.latency_seconds, default=None
    )
    if fastest_success:
        lines.append("")
        lines.append(f"fastest successful backend: {_printable(fastest_success.backend)} ({fastest_success.latency_seconds:.3f}s)")

    if graded:
        best_graded = max(
            (r for r in results if r.grade is not None), key=lambda r: r.grade.score, default=None
        )
        if best_graded:
            lines.append(f"highest-graded backend: {_printable(best_graded.backend)} ({best_graded.grade.score:.2f})")

    n_success = sum(1 for r in results if r.status == "success")
    n_error = len(results) - n_success
    lines.append(f"{n_success} succeeded, {n_error} failed")
    return "\n".join(lines)


def _seconds(value: Any) -> str:
    return "-" if value is None else f"{value:.3f}"


def _format_summary_table(summaries: list[BackendSummary], repeat: int) -> str:
    """One row per backend over every round of `--repeat`."""
    headers = ["backend", "ok", "p50 (s)", "p95 (s)", "min (s)", "max (s)", "stdev (s)", "errors"]
    rows = []
    for s in summaries:
        errors = ", ".join(f"{_printable(kind)} x{count}" for kind, count in s.errors.items()) or "-"
        rows.append(
            [
                _printable(s.backend),
                f"{s.successes}/{s.runs}",
                _seconds(s.latency_p50),
                _seconds(s.latency_p95),
                _seconds(s.latency_min),
                _seconds(s.latency_max),
                _seconds(s.latency_stdev),
                errors,
            ]
        )
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())

    answered = [s for s in summaries if s.latency_p50 is not None]
    if answered:
        fastest = min(answered, key=lambda s: s.latency_p50)
        lines.append("")
        lines.append(f"fastest backend by p50: {_printable(fastest.backend)} ({fastest.latency_p50:.3f}s)")
    lines.append(
        f"{repeat} round(s) x {len(summaries)} backend(s); latency over successful runs only"
    )
    return "\n".join(lines)


def _format_summary_csv(summaries: list[BackendSummary]) -> str:
    buf = io.StringIO()
    fieldnames = [f.name for f in fields(BackendSummary)]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for s in summaries:
        row = asdict(s)
        row["errors"] = json.dumps(row["errors"])
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return buf.getvalue().rstrip("\n")


_EXAMPLE = "examples/compare-mocks.yaml"

_EPILOG = """\
first run (offline, no API key - the example config uses mock backends):
  mch validate examples/compare-mocks.yaml
  mch run examples/compare-mocks.yaml --input '{"prompt": "a cat riding a bike"}'

exit codes:
  0    the run completed (backends may still have failed; see --fail-on-error)
  1    bad config or --input, --rubric unavailable, or --fail-on-error with a failed backend
  2    command-line usage error
  130  interrupted with Ctrl-C
"""


def _missing_file_hint(exc: ConfigError) -> str:
    """A config that does not exist is the likeliest first mistake; say what a
    working one looks like."""
    text = str(exc)
    if text.startswith(("no such file", "not a file")):
        text += f"  (a YAML file with a `backends:` list; try {_EXAMPLE} from a clone of the repo)"
    return text


def _cmd_validate(args: argparse.Namespace) -> None:
    try:
        backends = load_backends_from_file(args.config)
    except ConfigError as exc:
        print(f"INVALID: {_missing_file_hint(exc)}", file=sys.stderr)
        raise SystemExit(1)
    print(f"OK: {len(backends)} backend(s) configured: {', '.join(b.name for b in backends)}")


def _cmd_run(args: argparse.Namespace) -> None:
    try:
        backends = load_backends_from_file(args.config)
    except ConfigError as exc:
        print(f"error: {_missing_file_hint(exc)}", file=sys.stderr)
        raise SystemExit(1)

    try:
        params: dict[str, Any] = json.loads(args.input)
    except json.JSONDecodeError as exc:
        print(
            f"error: --input must be valid JSON: {exc}\n"
            "hint: quote the whole object so the shell keeps the inner double quotes - "
            "bash/zsh: --input '{\"prompt\": \"hi\"}'; "
            "PowerShell 5.1: --input '{\\\"prompt\\\": \\\"hi\\\"}'",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not isinstance(params, dict):
        print("error: --input must be a JSON object", file=sys.stderr)
        raise SystemExit(1)

    if args.rubric is not None and not build_judge_chain():
        print(
            "error: --rubric given but no judge model is configured "
            "(set NVIDIA_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY, GEMINI_API_KEY, "
            "or CEREBRAS_API_KEY) - checked once up front so a whole run of "
            "real backend calls isn't wasted only to find grading unavailable after.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if args.rubric is not None and not grading_extra_installed():
        # Same reason as the key check above: otherwise every backend runs
        # for real and each row only then says "grading unavailable", with
        # exit 0 and a "highest-graded backend" line computed from zeros.
        print(
            "error: --rubric needs the optional 'grading' extra (litellm), which is not installed - "
            "run `uv sync --extra grading` (or `pip install 'model-comparison-harness[grading]'`).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if args.repeat > 1:
        _run_repeated(args, backends, params)
        return

    # stdout carries only the table/JSON/CSV. Anything a backend or the judge
    # library prints while running (litellm prints a banner to stdout on every
    # failed judge call) goes to stderr, or `--json | jq` breaks.
    with contextlib.redirect_stdout(sys.stderr):
        results = asyncio.run(run_comparison(backends, params, timeout=args.timeout, rubric=args.rubric))

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    elif args.csv:
        print(_format_csv(results))
    else:
        print(_format_table(results))

    if args.fail_on_error and any(r.status == "error" for r in results):
        raise SystemExit(1)


def _run_repeated(args: argparse.Namespace, backends: list, params: dict[str, Any]) -> None:
    """`--repeat N`: N sequential rounds, then one summary line per backend."""
    with contextlib.redirect_stdout(sys.stderr):
        rounds = asyncio.run(run_repeated(backends, params, repeat=args.repeat, timeout=args.timeout))
    summaries = summarize(rounds)

    if args.json:
        print(
            json.dumps(
                {
                    "repeat": args.repeat,
                    "summary": [asdict(s) for s in summaries],
                    "runs": [[asdict(r) for r in round_results] for round_results in rounds],
                },
                indent=2,
            )
        )
    elif args.csv:
        print(_format_summary_csv(summaries))
    else:
        print(_format_summary_table(summaries, args.repeat))

    if args.fail_on_error and any(r.status == "error" for round_results in rounds for r in round_results):
        raise SystemExit(1)


def _repeat_count(value: str) -> int:
    """argparse type for --repeat: a whole number from 1 to 1000."""
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from None
    if not 1 <= count <= 1000:
        raise argparse.ArgumentTypeError(f"must be between 1 and 1000, got {count}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mch",
        description=(
            "Send the same JSON input to several model backends at once and compare "
            "latency, success and output side by side."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"mch {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="check a comparison config for structural problems")
    validate_parser.add_argument("config", help="YAML file with a `backends:` list")
    validate_parser.set_defaults(func=_cmd_validate)

    run_parser = subparsers.add_parser(
        "run", help="run the same input against every configured backend concurrently"
    )
    run_parser.add_argument("config", help=f"YAML file with a `backends:` list, e.g. {_EXAMPLE}")
    run_parser.add_argument("--input", required=True, help="JSON object, e.g. '{\"prompt\": \"a cat\"}'")
    output_format = run_parser.add_mutually_exclusive_group()
    output_format.add_argument("--json", action="store_true", help="print machine-readable JSON instead of a table")
    output_format.add_argument(
        "--csv", action="store_true", help="print CSV instead of a table (e.g. for a spreadsheet or `> file.csv`)"
    )
    run_parser.add_argument(
        "--rubric",
        default=None,
        help=(
            "plain-language grading criteria, e.g. 'mentions a red sneaker and no watermark text'. "
            "If given, every successful result is also scored by a judge model (llm-rubric style) - "
            "requires one of NVIDIA_API_KEY/GROQ_API_KEY/MISTRAL_API_KEY/GEMINI_API_KEY/CEREBRAS_API_KEY."
        ),
    )
    run_parser.add_argument(
        "--fail-on-error", action="store_true", help="exit non-zero if any backend errored"
    )
    run_parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=None,
        metavar="SECONDS",
        help=(
            "hard per-backend timeout enforced by the harness itself, on top of "
            "whatever a backend does internally - a hung or misbehaving backend is "
            "reported as a timeout error instead of blocking the whole comparison forever"
        ),
    )
    run_parser.add_argument(
        "--repeat",
        type=_repeat_count,
        default=1,
        metavar="N",
        help=(
            "run the whole comparison N times, one round after another, and print per "
            "backend: successes, p50/p95/min/max/stdev latency of the successful runs, and "
            "failures by error type (default: 1, a single run)"
        ),
    )
    run_parser.set_defaults(func=_cmd_run)

    args = parser.parse_args()
    if getattr(args, "repeat", 1) > 1 and getattr(args, "rubric", None) is not None:
        run_parser.error(
            "--rubric cannot be combined with --repeat yet (it would call the judge N times "
            "per backend); grade a single run instead"
        )
    try:
        args.func(args)
    except KeyboardInterrupt:
        # asyncio.run() has already cancelled every in-flight backend task
        # (and each backend's `finally` closed its HTTP client); only the
        # traceback is left to suppress. 130 = 128 + SIGINT, the shell norm.
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
