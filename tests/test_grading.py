from __future__ import annotations

import sys
import types

import pytest

from model_comparison_harness.grading import (
    GradeResult,
    GradingUnavailable,
    build_judge_chain,
    grade_result,
)


def _clear_all_judge_keys(monkeypatch):
    for env in ("NVIDIA_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(env, raising=False)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletionResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


@pytest.fixture
def fake_litellm(monkeypatch):
    """Install a fake `litellm` module in sys.modules so grading.py's lazy
    `import litellm` inside grade_result() resolves to it, without requiring
    the real (heavy, optional) package to be installed in the test env."""
    fake_module = types.SimpleNamespace()

    def _factory(acompletion):
        fake_module.acompletion = acompletion
        monkeypatch.setitem(sys.modules, "litellm", fake_module)
        return fake_module

    return _factory


def test_build_judge_chain_empty_when_no_keys_set(monkeypatch):
    _clear_all_judge_keys(monkeypatch)
    assert build_judge_chain() == []


def test_build_judge_chain_nvidia_carries_api_base(monkeypatch):
    _clear_all_judge_keys(monkeypatch)
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-key")

    chain = build_judge_chain()

    assert len(chain) == 1
    assert chain[0]["api_key"] == "nv-key"
    assert chain[0]["api_base"] == "https://integrate.api.nvidia.com/v1"


def test_build_judge_chain_preserves_declared_order(monkeypatch):
    _clear_all_judge_keys(monkeypatch)
    monkeypatch.setenv("CEREBRAS_API_KEY", "c-key")
    monkeypatch.setenv("GROQ_API_KEY", "g-key")

    chain = build_judge_chain()

    assert [entry["model"] for entry in chain] == ["groq/openai/gpt-oss-120b", "cerebras/gpt-oss-120b"]


@pytest.mark.asyncio
async def test_grade_result_raises_grading_unavailable_when_no_judge_configured(monkeypatch):
    _clear_all_judge_keys(monkeypatch)

    with pytest.raises(GradingUnavailable):
        await grade_result({"note": "x"}, rubric="should mention x")


@pytest.mark.asyncio
async def test_grade_result_raises_grading_unavailable_when_optional_extra_not_installed(monkeypatch):
    # The base test environment deliberately does not install the optional
    # `grading` extra (litellm) - this exercises that real ImportError path,
    # not a mocked one.
    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    monkeypatch.delitem(sys.modules, "litellm", raising=False)

    with pytest.raises(GradingUnavailable, match="grading"):
        await grade_result({"note": "x"}, rubric="anything")


@pytest.mark.asyncio
async def test_grade_result_parses_clean_judge_response(monkeypatch, fake_litellm):
    monkeypatch.setenv("GROQ_API_KEY", "g-key")

    async def fake_acompletion(**kwargs):
        return _FakeCompletionResponse('{"pass": true, "score": 0.9, "reason": "matches the rubric"}')

    fake_litellm(fake_acompletion)

    result = await grade_result({"note": "a red sneaker"}, rubric="mentions a red sneaker")

    assert result == GradeResult(passed=True, score=0.9, reason="matches the rubric")


@pytest.mark.asyncio
async def test_grade_result_degrades_gracefully_on_unparseable_judge_output(monkeypatch, fake_litellm):
    monkeypatch.setenv("GROQ_API_KEY", "g-key")

    async def fake_acompletion(**kwargs):
        return _FakeCompletionResponse("sure, I think this passes!")

    fake_litellm(fake_acompletion)

    result = await grade_result({"note": "x"}, rubric="anything")

    assert result.passed is False
    assert result.score == 0.0
    assert "unparseable" in result.reason


@pytest.mark.asyncio
async def test_grade_result_sends_rubric_and_output_to_judge(monkeypatch, fake_litellm):
    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeCompletionResponse('{"pass": false, "score": 0.1, "reason": "no match"}')

    fake_litellm(fake_acompletion)

    await grade_result({"text": "a blue shoe"}, rubric="must mention a red sneaker")

    user_message = captured["messages"][1]["content"]
    assert "must mention a red sneaker" in user_message
    assert "a blue shoe" in user_message
    assert captured["model"] == "groq/openai/gpt-oss-120b"
