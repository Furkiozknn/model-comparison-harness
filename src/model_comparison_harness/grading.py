"""Optional LLM-judge scoring for comparison results, in the spirit of
promptfoo's `llm-rubric` assertion type (design pattern only - no promptfoo
code here, this is an independent implementation).

Instead of only latency/success/failure, a plain-language rubric ("the
response should mention a red sneaker and not contain any watermark text")
gets sent to a judge model alongside a backend's output, and the judge
returns a pass/fail verdict, a 0-1 score, and a one-sentence reason.

Entirely optional: nothing else in this package depends on this module, and
comparisons work exactly as before if no rubric is requested. The judge
provider chain intentionally reuses the same shape and provider list already
proven in nvidia-nim-mcp (NVIDIA NIM first, then Groq/Mistral/Gemini/Cerebras,
whichever has an API key set) - not imported from that repo (these are
independent projects in the same ecosystem, coupled only through documented
contracts, never a shared Python dependency), just the same well-tested
"try several genuinely-free providers in order" idea.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional


class GradingUnavailable(Exception):
    """Raised when no judge provider is configured (no relevant API key set)
    or the optional `grading` extra isn't installed."""


@dataclass
class GradeResult:
    passed: bool
    score: float  # 0.0-1.0
    reason: str


# Same provider list/order nvidia-nim-mcp's EXTRA_PROVIDERS uses, so a user
# who already has one of these keys set for that project gets judge grading
# here for free. Model names on free/preview tiers drift - see that repo's
# own comments for the "confirmed working" verification discipline this
# list should also follow as it's revisited.
_JUDGE_PROVIDERS: list[dict[str, str]] = [
    {
        "env": "NVIDIA_API_KEY",
        "model": "openai/nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "api_base": "https://integrate.api.nvidia.com/v1",
    },
    {"env": "GROQ_API_KEY", "model": "groq/openai/gpt-oss-120b"},
    {"env": "MISTRAL_API_KEY", "model": "mistral/mistral-small-latest"},
    {"env": "GEMINI_API_KEY", "model": "gemini/gemini-flash-latest"},
    {"env": "CEREBRAS_API_KEY", "model": "cerebras/gpt-oss-120b"},
]

_RUBRIC_SYSTEM_PROMPT = (
    "You are grading a model's output against a rubric. Respond with ONLY a "
    'JSON object of the exact shape {"pass": true or false, "score": a '
    'number from 0.0 to 1.0, "reason": "one short sentence"}. No markdown '
    "code fences, no other text before or after the JSON."
)


def build_judge_chain() -> list[dict[str, Any]]:
    """Chain of {model, api_key[, api_base]} entries for whichever configured
    judge provider has its API key actually present in the environment right
    now - skipped silently if unconfigured, same rule as every other
    provider chain in this ecosystem."""
    chain = []
    for provider in _JUDGE_PROVIDERS:
        key = os.environ.get(provider["env"])
        if not key:
            continue
        entry: dict[str, Any] = {"model": provider["model"], "api_key": key}
        if "api_base" in provider:
            entry["api_base"] = provider["api_base"]
        chain.append(entry)
    return chain


async def grade_result(output: Any, rubric: str) -> GradeResult:
    """Grade `output` (any JSON-serializable value) against `rubric` using
    whichever judge provider is configured. Raises GradingUnavailable if no
    provider is configured or the optional `grading` extra isn't installed -
    callers should treat that as a one-time config problem to report clearly,
    not silently degrade every graded row."""
    chain = build_judge_chain()
    if not chain:
        raise GradingUnavailable(
            "no judge model configured - set one of: " + ", ".join(p["env"] for p in _JUDGE_PROVIDERS)
        )

    try:
        import litellm
    except ImportError as exc:
        raise GradingUnavailable(
            "the optional 'grading' extra isn't installed - run `uv sync --extra grading`"
        ) from exc

    primary, fallbacks = chain[0], chain[1:]
    messages = [
        {"role": "system", "content": _RUBRIC_SYSTEM_PROMPT},
        {"role": "user", "content": f"Rubric: {rubric}\n\nOutput to grade:\n{json.dumps(output)}"},
    ]
    response = await litellm.acompletion(
        messages=messages,
        max_tokens=200,
        fallbacks=fallbacks or None,
        **primary,
    )
    content = response.choices[0].message.content
    try:
        data = json.loads(content)
        return GradeResult(passed=bool(data["pass"]), score=float(data["score"]), reason=str(data.get("reason", "")))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # The judge didn't return clean JSON - degrade to an ungraded,
        # clearly-labeled result rather than crashing the whole comparison
        # over a formatting slip from the judge model itself.
        return GradeResult(passed=False, score=0.0, reason=f"judge returned unparseable output: {content[:200]!r}")
