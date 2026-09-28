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
    assert reader.fieldnames == ["backend", "status", "latency_seconds", "result", "error", "error_type", "grade"]
    assert all(row["grade"] == "" for row in rows)  # no --rubric, so no grade cell
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
    monkeypatch.setattr("model_comparison_harness.cli.grading_extra_installed", lambda: True)

    _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--rubric", "should be fast"])

    out = capsys.readouterr().out
    assert "grade" in out
    assert "PASS 0.80" in out
    assert "highest-graded backend: fast" in out


def test_run_with_rubric_fails_fast_when_the_grading_extra_is_missing(monkeypatch, capsys, config_file):
    # Regression: with a judge key set but litellm not installed (the default
    # after a plain `uv sync`), every backend ran, every row read "grading
    # unavailable", and the exit code was 0.
    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    monkeypatch.setattr("model_comparison_harness.cli.grading_extra_installed", lambda: False)

    async def must_not_run(*args, **kwargs):
        raise AssertionError("backends ran before the grading extra was checked")

    monkeypatch.setattr("model_comparison_harness.cli.run_comparison", must_not_run)
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--rubric", "anything"])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "uv sync --extra grading" in captured.err
    assert captured.out == ""


# --- exit codes and argument validation --------------------------------------


@pytest.mark.parametrize("value", ["0", "-5", "nan", "inf", "soon"])
def test_run_rejects_timeout_that_is_not_a_positive_number(monkeypatch, capsys, config_file, value):
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["run", str(config_file), "--input", "{}", f"--timeout={value}"])
    assert exc_info.value.code == 2  # argparse usage error
    assert "--timeout" in capsys.readouterr().err


def test_validate_reports_bad_field_as_invalid_not_a_traceback(monkeypatch, capsys, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("backends:\n  - name: a\n    type: mock\n    delay: fast\n")
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["validate", str(path)])
    assert exc_info.value.code == 1
    assert "INVALID: backend 'a' (type=mock): 'delay' must be a number >= 0" in capsys.readouterr().err


def test_ctrl_c_exits_130_without_a_traceback(monkeypatch, capsys, config_file):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("model_comparison_harness.cli.run_comparison", interrupted)
    with pytest.raises(SystemExit) as exc_info:
        _run(monkeypatch, ["run", str(config_file), "--input", "{}"])
    assert exc_info.value.code == 130
    assert capsys.readouterr().err.strip() == "interrupted"


# --- output schema -------------------------------------------------------------
# Scripts parse --json/--csv. Renaming, adding or reordering a field is a
# breaking change for them, so it has to show up as a failing test here.

_RESULT_FIELDS = ["backend", "status", "latency_seconds", "result", "error", "error_type", "grade"]


def test_json_output_schema_is_pinned(monkeypatch, capsys, config_file):
    monkeypatch.setenv("GROQ_API_KEY", "g-key")

    async def fake_grade_result(output, rubric):
        from model_comparison_harness.grading import GradeResult

        return GradeResult(passed=True, score=0.5, reason="ok")

    monkeypatch.setattr("model_comparison_harness.runner.grade_result", fake_grade_result)
    monkeypatch.setattr("model_comparison_harness.cli.grading_extra_installed", lambda: True)
    _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--json", "--rubric", "r"])
    rows = json.loads(capsys.readouterr().out)

    assert [list(row) for row in rows] == [_RESULT_FIELDS, _RESULT_FIELDS]
    ok, failed = rows
    assert ok["status"] == "success" and isinstance(ok["latency_seconds"], float)
    assert ok["error"] is None and ok["error_type"] is None
    assert ok["grade"] == {"passed": True, "score": 0.5, "reason": "ok"}
    assert failed["status"] == "error" and failed["result"] is None and failed["grade"] is None
    assert failed["error"] == "simulated failure" and failed["error_type"] == "BackendError"


def test_csv_grade_cell_is_the_same_json_object_as_in_json_output(monkeypatch, capsys, config_file):
    monkeypatch.setenv("GROQ_API_KEY", "g-key")

    async def fake_grade_result(output, rubric):
        from model_comparison_harness.grading import GradeResult

        return GradeResult(passed=False, score=0.25, reason="meh")

    monkeypatch.setattr("model_comparison_harness.runner.grade_result", fake_grade_result)
    monkeypatch.setattr("model_comparison_harness.cli.grading_extra_installed", lambda: True)
    _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--csv", "--rubric", "r"])
    reader = csv.DictReader(io.StringIO(capsys.readouterr().out))
    rows = list(reader)
    assert reader.fieldnames == _RESULT_FIELDS
    assert json.loads(rows[0]["grade"]) == {"passed": False, "score": 0.25, "reason": "meh"}
    assert rows[1]["grade"] == "" and rows[1]["error_type"] == "BackendError"


def test_table_escapes_control_characters_from_backend_and_judge_text():
    # Regression: error text (from a remote server) and judge reasons were
    # printed raw, so ESC sequences reached the terminal and a newline split
    # the row.
    from model_comparison_harness.cli import _format_table
    from model_comparison_harness.grading import GradeResult
    from model_comparison_harness.runner import ComparisonResult

    table = _format_table(
        [
            ComparisonResult("bad", "error", 0.1, error="x\x1b[2J\x1b]0;pwned\x07\nsecond line", error_type="BackendError"),
            ComparisonResult("ok", "success", 0.2, result={"t": "\x1b[31m"}, grade=GradeResult(True, 1.0, "fine\x1b[0m\r")),
        ]
    )
    assert "\x1b" not in table and "\x07" not in table and "\r" not in table
    assert "ERROR: x\\x1b[2J\\x1b]0;pwned\\x07\\nsecond line" in table
    assert "PASS 1.00 - fine\\x1b[0m\\r" in table
    # 2 header lines + 2 rows + blank + fastest + highest-graded + totals
    assert len(table.splitlines()) == 8


def test_json_stdout_stays_parseable_when_something_prints_during_the_run(monkeypatch, capsys, config_file):
    # Regression: litellm prints "Give Feedback / Get Help ..." to stdout on a
    # failed judge call, which corrupted `--json`/`--csv` output.
    from model_comparison_harness import runner

    real_run_one = runner._run_one

    async def chatty_run_one(*args, **kwargs):
        print("\x1b[1;31mGive Feedback / Get Help: https://example.invalid\x1b[0m")
        return await real_run_one(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_one", chatty_run_one)
    _run(monkeypatch, ["run", str(config_file), "--input", "{}", "--json"])
    captured = capsys.readouterr()
    rows = json.loads(captured.out)
    assert [row["backend"] for row in rows] == ["fast", "broken"]
    assert "Give Feedback" in captured.err
