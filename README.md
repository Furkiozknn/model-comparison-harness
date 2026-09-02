# model-comparison-harness

Run the same request against multiple generative-model backends **concurrently** and compare latency, success/failure, and results side by side — a small CLI (`mch`) for the "which model should this capability actually route to" question.

This extends the same lesson [`nvidia-nim-mcp`](https://github.com/Furkiozknn/nvidia-nim-mcp) already lives by (try more than one model, don't trust any single one to stay fast/available/alive) into an explicit, on-demand comparison tool: point it at N backends, fire the same input at all of them at once, see exactly how they stack up.

It's part of a small ecosystem of focused repos for an AI creative platform — one of its backend types (`gateway`) speaks the same submit/poll HTTP contract as [`ai-job-gateway`](https://github.com/Furkiozknn/ai-job-gateway), so you can compare a mock/local model against a real one running behind that gateway with no code, just YAML.

## A comparison config

```yaml
# examples/compare-mocks.yaml
backends:
  - name: fast-mock
    type: mock
    delay: 0.1
    result:
      note: "simulates a quick, cheap model"

  - name: slow-mock
    type: mock
    delay: 1.2
    result:
      note: "simulates a slower, higher-quality model"

  - name: flaky-mock
    type: mock
    delay: 0.3
    should_fail: true
    failure_message: "simulates a backend that is currently down or rate-limited"
```

```bash
uv sync
uv run mch run examples/compare-mocks.yaml --input '{"prompt": "a cat riding a bike"}'
```

```
backend      status   latency (s)  summary
-----------  -------  -----------  ------------------------------------------------------------
fast-mock    success  0.101        {"note": "simulates a quick, cheap model", "params_receive...
slow-mock    success  1.201        {"note": "simulates a slower, higher-quality model", "param...
flaky-mock   error    0.301        ERROR: simulates a backend that is currently down or rate-l...

fastest successful backend: fast-mock (0.101s)
2 succeeded, 1 failed
```

One backend erroring never hides the other results — the whole point is seeing every backend's outcome side by side, including the failures. The same goes for a backend that just *hangs*: pass `--timeout SECONDS` and a backend that exceeds it is reported as a timeout error instead of blocking every other backend's result forever (see "The CLI" below).

## Backend types

| Type | What it does | Required fields |
|---|---|---|
| `mock` | Configurable delay + fixed (or forced-failing) result. Zero network, zero dependencies — for tests, demos, and dry-running a config's shape. | — |
| `gateway` | `POST /v1/{capability}` + poll, the same submit/poll contract [`ai-job-gateway`](https://github.com/Furkiozknn/ai-job-gateway) implements. Works against any server implementing that same shape, not only that specific repo. | `url`, `capability` |
| `http` | The simplest real-world case: `POST` params to a fixed URL, treat the JSON response body as the result directly — no submit/poll assumed. Fits any synchronous request/response API. | `url` |

See `examples/compare-with-gateway.yaml` for a config comparing a local mock against a real running `ai-job-gateway` server.

## The CLI

```bash
uv run mch validate config.yaml
# OK: 3 backend(s) configured: fast-mock, slow-mock, flaky-mock

uv run mch run config.yaml --input '{"prompt": "..."}'                  # table output
uv run mch run config.yaml --input '{"prompt": "..."}' --json           # machine-readable, one object per backend
uv run mch run config.yaml --input '{"prompt": "..."}' --csv            # CSV, e.g. `--csv > results.csv` for a spreadsheet
uv run mch run config.yaml --input '{"prompt": "..."}' --fail-on-error  # exit 1 if any backend errored (useful in CI)
uv run mch run config.yaml --input '{"prompt": "..."}' --timeout 10     # hard per-backend ceiling enforced by the harness
```

`--json` and `--csv` are mutually exclusive (pick one machine-readable format at a time); with neither, you get the human-readable table.

Every result — table, JSON, and CSV alike — carries an `error_type` alongside `error` for failures: the failing exception's class name (`"BackendError"`, `"TimeoutError"`, or whatever a custom backend raises), so a script can branch on the *kind* of failure without parsing the message string. `--timeout` is enforced by the harness itself, independently of any timeout a backend already applies internally (e.g. `gateway`'s and `http`'s own `timeout:` config field) — it exists specifically to bound a backend that doesn't time out on its own, whether that's a bug in a custom `Backend` subclass or a server that simply never responds.

## Using it as a library

```python
import asyncio
from model_comparison_harness import load_backends_from_file, run_comparison

backends = load_backends_from_file("examples/compare-mocks.yaml")
results = asyncio.run(run_comparison(backends, {"prompt": "a cat riding a bike"}, timeout=10))
for r in results:
    print(r.backend, r.status, r.latency_seconds, r.result or r.error, r.error_type)
```

## Writing a new backend type

Implement the `Backend` interface (one async method) and register it in `config.py`'s `_BUILDERS` dict:

```python
from model_comparison_harness import Backend

class MyBackend(Backend):
    async def run(self, params: dict) -> dict:
        ...  # call your model, return a JSON-serializable result, or raise
```

## Development

```bash
uv sync --group dev
uv run pytest
```

Fully async (`pytest-asyncio`), no real network needed — `gateway` and `http` backends are tested against `httpx.MockTransport`. One test specifically asserts backends actually run concurrently (three 0.2s-delay mocks finish in well under 0.6s total), since sequential execution would make the whole comparison's latency numbers meaningless. 44 tests as of this writing.

## Limitations

- **Latency includes this process's own overhead** (event loop scheduling, JSON encode/decode) on top of each backend's real network/inference time — fine for relative "which is faster" comparisons between backends run side by side in the same process, not a substitute for a dedicated load-testing tool if you need absolute numbers.
- **One input per run.** `mch run` fires a single `--input` payload at every backend once; there's no built-in sweep over a list of prompts or repeated trials for statistical confidence (score with `--json`/`--csv` output piped into your own script if you need that).
- **No retries.** A backend that fails or times out is reported as a single failed row, not retried — matching this tool's job (see how backends behave *right now*, including failures) rather than a production request pipeline's job.
- **`gateway` and `http` backends make real HTTP calls** to whatever `url:` you configure; nothing stops you from pointing a config at an untrusted or unintended endpoint, so treat comparison configs with the same care as any other file that names a URL to POST arbitrary `--input` JSON to.

## License

MIT — see [LICENSE](LICENSE).
