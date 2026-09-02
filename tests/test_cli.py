from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from model_comparison_harness.cli import main

CONFIG_YAML = """
backends:
  - name: fast
    type: mock
    delay: 0
    result:
      note: fast one
  - name: broken
    type: mock
    delay: 0
    should_fail: true
    failure_message: "simulated failure"
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "compare.yaml"
    path.write_text(CONFIG_YAML)
    return path


def _run(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["mch", *argv])
    main()


def test_validate_ok(monkeypatch, capsys, config_file):
    _run(monkeypatch, ["validate", str(config_file)])
    out = capsys.readouterr().out
    assert "OK: 2 backend(s) configured: fast, broken" in out


def test_validate_bad_config_exits_nonzero(monkeypatch, capsys, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("backends: []\n")
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["validate", str(path)])
    assert exc_info.value.code == 1
    assert "INVALID" in capsys.readouterr().err


def test_run_table_output(monkeypatch, capsys, config_file):
    _run(monkeypatch, ["run", str(config_file), "--input", '{"prompt": "hi"}'])
    out = capsys.readouterr().out
    assert "fast" in out
    assert "broken" in out
    assert "success" in out
    assert "error" in out
    assert "1 succeeded, 1 failed" in out
    assert "fastest successful backend: fast" in out


def test_run_json_output(monkeypatch, capsys, config_file):
    _run(monkeypatch, ["run", str(config_file), "--input", '{"prompt": "hi"}', "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 2
    names = {row["backend"] for row in data}
    assert names == {"fast", "broken"}
    fast_row = next(r for r in data if r["backend"] == "fast")
    assert fast_row["status"] == "success"
    assert fast_row["result"]["note"] == "fast one"


def test_run_invalid_json_input_exits_nonzero(monkeypatch, capsys, config_file):
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["run", str(config_file), "--input", "not json"])
    assert exc_info.value.code == 1
    assert "must be valid JSON" in capsys.readouterr().err


def test_run_non_object_json_input_exits_nonzero(monkeypatch, capsys, config_file):
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["run", str(config_file), "--input", "[1, 2, 3]"])
    assert exc_info.value.code == 1
    assert "must be a JSON object" in capsys.readouterr().err


def test_run_fail_on_error_exits_nonzero_when_any_backend_errors(monkeypatch, capsys, config_file):
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--fail-on-error"])
    assert exc_info.value.code == 1


def test_run_without_fail_on_error_exits_zero_even_with_a_failing_backend(monkeypatch, capsys, config_file):
    _run(monkeypatch, ["run", str(config_file), "--input", "{}"])  # should not raise SystemExit


def test_run_csv_output(monkeypatch, capsys, config_file):
    _run(monkeypatch, ["run", str(config_file), "--input", '{"prompt": "hi"}', "--csv"])
    out = capsys.readouterr().out
    reader = csv.DictReader(io.StringIO(out))
    rows = list(reader)
    assert reader.fieldnames == ["backend", "status", "latency_seconds", "result", "error", "error_type"]
    assert {row["backend"] for row in rows} == {"fast", "broken"}
    fast_row = next(r for r in rows if r["backend"] == "fast")
    assert fast_row["status"] == "success"
    assert json.loads(fast_row["result"])["note"] == "fast one"
    broken_row = next(r for r in rows if r["backend"] == "broken")
    assert broken_row["status"] == "error"
    assert broken_row["error"] == "simulated failure"


def test_run_json_and_csv_are_mutually_exclusive(monkeypatch, capsys, config_file):
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--json", "--csv"])
    assert "not allowed" in capsys.readouterr().err


def test_run_timeout_reports_slow_backend_as_error(monkeypatch, capsys, tmp_path):
    slow_config = tmp_path / "slow.yaml"
    slow_config.write_text(
        "backends:\n"
        "  - name: slow\n"
        "    type: mock\n"
        "    delay: 0.3\n"
        "  - name: fast\n"
        "    type: mock\n"
        "    delay: 0\n"
    )
    _run(monkeypatch, ["run", str(slow_config), "--input", "{}", "--json", "--timeout", "0.05"])
    data = json.loads(capsys.readouterr().out)
    slow_row = next(r for r in data if r["backend"] == "slow")
    fast_row = next(r for r in data if r["backend"] == "fast")
    assert slow_row["status"] == "error"
    assert slow_row["error_type"] == "TimeoutError"
    assert fast_row["status"] == "success"
