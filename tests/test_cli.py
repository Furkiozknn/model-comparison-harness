from __future__ import annotations

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


# --- --rubric --------------------------------------------------------------

def test_run_with_rubric_but_no_judge_configured_exits_nonzero_before_running_backends(
    monkeypatch, capsys, config_file
):
    for env in ("NVIDIA_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(env, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--rubric", "anything"])

    assert exc_info.value.code == 1
    assert "no judge model is configured" in capsys.readouterr().err


def test_run_with_empty_string_rubric_still_fails_fast_when_no_judge_configured(
    monkeypatch, capsys, config_file
):
    # `--rubric ""` is a truthy-looking edge case: args.rubric == "" is
    # falsy, but the user explicitly passed the flag, so the same fail-fast
    # check must still fire rather than silently running every backend for
    # real and only reporting "grading unavailable" per row afterward.
    for env in ("NVIDIA_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(env, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--rubric", ""])

    assert exc_info.value.code == 1
    assert "no judge model is configured" in capsys.readouterr().err


def test_run_with_rubric_and_configured_judge_shows_grade_column(monkeypatch, capsys, config_file):
    monkeypatch.setenv("GROQ_API_KEY", "g-key")

    async def fake_grade_result(output, rubric):
        from model_comparison_harness.grading import GradeResult

        return GradeResult(passed=True, score=0.8, reason="looks right")

    monkeypatch.setattr("model_comparison_harness.runner.grade_result", fake_grade_result)

    _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--rubric", "should be fast"])

    out = capsys.readouterr().out
    assert "grade" in out
    assert "PASS 0.80" in out
    assert "highest-graded backend: fast" in out
