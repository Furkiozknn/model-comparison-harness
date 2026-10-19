# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The version here, in
`pyproject.toml` and in `project-meta.json` is the same; the release workflow
(`.github/workflows/yayinla.yml`) refuses a tag that does not match it.

## [Unreleased]

### Changed
- `gateway` backend: a poll GET that hits a network error or a 502/503/504
  is retried up to three times with backoff (0.5 s, 1 s, 2 s, never past
  `timeout`). The submission and the job are never repeated.
- Re-vendored `gateway_poll.py` from ai-job-gateway: a malformed 2xx
  submission raises `GatewayContractError` instead of `assert`/`KeyError`.
  The backend's error message is unchanged ("submission response is
  missing 'id'").

### Added
- `mch run --repeat N` (1-1000) runs the comparison N times, one round
  after another, and prints per backend: successes, p50/p95/min/max/stdev
  latency over the successful runs, and failures by `error_type`. `--json`
  carries the summary plus every round; `--csv` the summary rows.
  `--fail-on-error` covers every round; `--rubric` with `--repeat` is a
  usage error for now. `--repeat 1` (default) leaves the output unchanged.
  Library: `run_repeated()`, `stats.summarize()`, `stats.percentile()`.

## [0.1.0] - 2026-09-25

First release.

### Added

- `mch run` fires one `--input` JSON object at every backend in a YAML config
  concurrently and reports latency, status and result per backend as a table,
  `--json` or `--csv`.
- Backend types `mock`, `gateway` (the ai-job-gateway submit/poll contract)
  and `http` (plain synchronous POST).
- `mch validate` checks a config without running it.
- `--timeout SECONDS`: a harness-enforced per-backend ceiling. A backend that
  exceeds it is cancelled and reported as `error_type: TimeoutError`.
- `--fail-on-error` exits 1 if any backend failed.
- Every failure carries `error_type` (the exception class name).
- `--rubric`: optional judge-model grading (pass/fail, 0-1 score, reason)
  through the `grading` extra (`uv sync --extra grading`).
- Library API: `load_backends_from_file`, `run_comparison`, `Backend`.

### Security

- A gateway `capability` must match `^[A-Za-z0-9_-]+$` (it becomes a URL
  path segment).
- Backend output sent to the judge is fenced by a per-call random marker and
  labelled as data.
- `http` backends: responses over `max_response_bytes` (default 10 MiB) are
  rejected while streaming, redirects are reported instead of followed, and
  error bodies quoted into `error` are clipped to 500 characters.
- The judge call has a 120 s ceiling, and a score outside 0-1 is reported as
  unparseable. `pass` must be a JSON boolean and `score` a JSON number: a
  judge answering `"pass": "false"` used to be recorded as a PASS.
- `gateway` backends read submission and poll bodies under the same
  `max_response_bytes` cap as `http` (they had no cap), and a server-chosen
  `polling_url` must be a path on the configured host and port
  (`"@other-host/x"` used to send the next request to `other-host`).
- `timeout` on `gateway` and `http` backends is a total wall-clock ceiling;
  a response trickling in under httpx's per-read timeout could hold a run
  open indefinitely.
- The table escapes control characters in error text, judge reasons and
  backend names, so a backend's output cannot drive the terminal.
- A gateway job's error text is prefixed with `job failed: ` and clipped, so
  a server-chosen string never starts a table or CSV cell.

### Config validation

- Unknown fields are rejected with a "did you mean" hint, and each field's
  type and range is checked (`delay`, `timeout`, `poll_interval`,
  `max_response_bytes`, `headers`, `result`, `should_fail`, `url` scheme).
  These configs used to pass `mch validate` and then crash or quietly use a
  default in `mch run`.
- A directory, unreadable file or non-UTF-8 file is a config error (exit 1)
  instead of a traceback.
- `--timeout` must be a number greater than 0 (exit 2 otherwise).
- Ctrl-C exits 130 without a traceback.
- `max_response_bytes` must be a whole number (`1.5` was truncated to 1).
- Malformed gateway responses (non-JSON, missing `id`, a poll body that is not
  an object) are a `BackendError` naming the problem, not a bare
  `JSONDecodeError`, `KeyError` or `AttributeError`.
- A backend type registered only in `_BUILDERS`, as the README describes, loads
  again instead of failing with `KeyError`.
- `--rubric` without the `grading` extra installed fails up front (exit 1),
  like a missing judge key, instead of running every backend and grading each
  row "unavailable" with exit 0.
- stdout carries only the table/JSON/CSV. litellm printed a "Give Feedback"
  banner to stdout on every failed judge call, which broke `--json` and
  `--csv` output; output printed during the run now goes to stderr and the
  banner is switched off.

### CI

- Tests on Python 3.11, 3.12 and 3.13, plus the README quick start commands.
- A package job builds the sdist and wheel, runs `twine check --strict`,
  installs the wheel into an empty environment, runs the example and checks
  that `__version__` matches `pyproject.toml`.

[0.1.0]: https://github.com/Furkiozknn/model-comparison-harness/releases/tag/v0.1.0
