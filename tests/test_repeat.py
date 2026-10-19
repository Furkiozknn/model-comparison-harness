"""`--repeat N`: repeated rounds, latency percentiles and success rate."""

from __future__ import annotations

import csv
import io
import json
import statistics

import pytest

from model_comparison_harness.backends import MockBackend
from model_comparison_harness.cli import main
from model_comparison_harness.runner import ComparisonResult, run_repeated
from model_comparison_harness.stats import percentile, summarize


def test_percentile_matches_linear_interpolation():
    data = [0.5, 0.1, 0.4, 0.2, 0.3]
    # statistics.quantiles(method="inclusive") is the same type-7 definition.
    cuts = statistics.quantiles(data, n=100, method="inclusive")
    assert percentile(data, 50) == pytest.approx(cuts[49])
    assert percentile(data, 95) == pytest.approx(cuts[94])
    assert percentile(data, 0) == 0.1
    assert percentile(data, 100) == 0.5
    assert percentile([7.0], 95) == 7.0


@pytest.mark.parametrize("values, q", [([], 50), ([1.0], -1), ([1.0], 101)])
def test_percentile_rejects_bad_input(values, q):
    with pytest.raises(ValueError):
        percentile(values, q)


def _row(backend, status, latency, error_type=None):
    return ComparisonResult(
        backend=backend,
        status=status,
        latency_seconds=latency,
        error="x" if status == "error" else None,
        error_type=error_type,
    )


def test_summary_uses_successful_runs_for_latency_and_counts_failures_by_type():
    rounds = [
        [_row("a", "success", 0.1), _row("b", "error", 5.0, "TimeoutError")],
        [_row("a", "success", 0.3), _row("b", "success", 1.0)],
        [_row("a", "error", 0.0, "BackendError"), _row("b", "error", 5.0, "TimeoutError")],
    ]
    a, b = summarize(rounds)
    assert (a.backend, a.runs, a.successes) == ("a", 3, 2)
    assert a.success_rate == pytest.approx(2 / 3)
    assert a.latency_p50 == pytest.approx(0.2)  # the 0.0s failure is not a sample
    assert a.latency_min == 0.1 and a.latency_max == 0.3
    assert a.errors == {"BackendError": 1}
    assert b.successes == 1 and b.latency_p50 == 1.0 and b.latency_stdev == 0.0
    assert b.errors == {"TimeoutError": 2}  # the 5 s timeouts did not become latency


def test_a_backend_that_never_succeeded_has_no_latency():
    (s,) = summarize([[_row("down", "error", 0.2, "BackendError")]] * 4)
    assert s.successes == 0 and s.success_rate == 0
    assert s.latency_p50 is None and s.latency_p95 is None
    assert s.errors == {"BackendError": 4}


async def test_run_repeated_runs_every_round():
    backends = [MockBackend("a", delay_seconds=0), MockBackend("b", delay_seconds=0, should_fail=True)]
    rounds = await run_repeated(backends, {"x": 1}, repeat=4)
    assert len(rounds) == 4
    assert all([r.backend for r in round_] == ["a", "b"] for round_ in rounds)
    with pytest.raises(ValueError):
        await run_repeated(backends, {"x": 1}, repeat=0)


CONFIG = """
backends:
  - name: fast
    type: mock
    delay: 0
  - name: down
    type: mock
    delay: 0
    should_fail: true
"""


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return str(path)


def _cli(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["mch", *argv])
    main()


def test_cli_repeat_prints_a_summary_table(monkeypatch, capsys, config_path):
    _cli(monkeypatch, "run", config_path, "--input", '{"x": 1}', "--repeat", "5")
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split() == ["backend", "ok", "p50", "(s)", "p95", "(s)", "min", "(s)", "max", "(s)", "stdev", "(s)", "errors"]
    assert any(line.startswith("fast ") and "5/5" in line for line in lines)
    assert any(line.startswith("down ") and "0/5" in line and "BackendError x5" in line for line in lines)
    assert "fastest backend by p50: fast" in out
    assert "5 round(s) x 2 backend(s)" in out


def test_cli_repeat_json_has_summary_and_every_run(monkeypatch, capsys, config_path):
    _cli(monkeypatch, "run", config_path, "--input", '{"x": 1}', "--repeat", "3", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["repeat"] == 3
    assert [s["backend"] for s in payload["summary"]] == ["fast", "down"]
    assert payload["summary"][0]["successes"] == 3
    assert len(payload["runs"]) == 3 and len(payload["runs"][0]) == 2
    assert set(payload["summary"][0]) >= {"latency_p50", "latency_p95", "success_rate", "errors"}


def test_cli_repeat_csv_has_one_row_per_backend(monkeypatch, capsys, config_path):
    _cli(monkeypatch, "run", config_path, "--input", '{"x": 1}', "--repeat", "2", "--csv")
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert [r["backend"] for r in rows] == ["fast", "down"]
    assert rows[1]["latency_p50"] == ""  # never succeeded
    assert json.loads(rows[1]["errors"]) == {"BackendError": 2}


def test_cli_repeat_fail_on_error_checks_every_round(monkeypatch, capsys, config_path):
    with pytest.raises(SystemExit) as exc_info:
        _cli(monkeypatch, "run", config_path, "--input", '{"x": 1}', "--repeat", "2", "--fail-on-error")
    assert exc_info.value.code == 1


def test_cli_repeat_one_keeps_the_single_run_output(monkeypatch, capsys, config_path):
    _cli(monkeypatch, "run", config_path, "--input", '{"x": 1}', "--repeat", "1", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list) and payload[0]["backend"] == "fast"


@pytest.mark.parametrize("value", ["0", "-3", "1.5", "lots", "1001"])
def test_cli_repeat_rejects_bad_counts(monkeypatch, capsys, config_path, value):
    with pytest.raises(SystemExit) as exc_info:
        _cli(monkeypatch, "run", config_path, "--input", '{"x": 1}', "--repeat", value)
    assert exc_info.value.code == 2


def test_cli_repeat_with_rubric_is_a_usage_error(monkeypatch, capsys, config_path):
    with pytest.raises(SystemExit) as exc_info:
        _cli(monkeypatch, "run", config_path, "--input", '{"x": 1}', "--repeat", "3", "--rubric", "ok")
    assert exc_info.value.code == 2
    assert "--rubric cannot be combined with --repeat" in capsys.readouterr().err
