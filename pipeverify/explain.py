"""Optional plain-English explanation of a result by an LLM. Off unless asked for.

Only the findings are sent: check names, counts, percentages and column names.
Example values from the data are removed first unless `include_examples` is set.
"""
from __future__ import annotations

import json
import os

from .core import RunResult

PROMPT = """You review data changes for a data engineering team.
Below is the output of pipeverify, which compared tables before and after a pipeline change.
Write a short explanation (at most 120 words) for the engineer who made the change:
1. What most likely happened to the data, in plain words.
2. Which finding matters most and why.
3. One concrete thing to check or fix next.
Do not invent numbers. If the verdict is PASS, say so in one sentence.

Result:
{payload}
"""


class ExplainError(RuntimeError):
    pass


def _payload(result: RunResult, include_examples: bool) -> str:
    data = result.to_dict()
    data.pop("explanation", None)
    for t in data["tables"]:
        for f in t["findings"]:
            if not include_examples:
                f.get("metrics", {}).pop("examples", None)
    return json.dumps(data, default=str)


def explain(result: RunResult, provider: str | None = None, model: str | None = None,
            include_examples: bool = False) -> str:
    provider = (provider or os.environ.get("PIPEVERIFY_LLM_PROVIDER") or "").lower()
    if not provider:
        provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else (
            "openai" if os.environ.get("OPENAI_API_KEY") else "")
    if not provider:
        raise ExplainError("Set ANTHROPIC_API_KEY or OPENAI_API_KEY to use --explain")
    prompt = PROMPT.format(payload=_payload(result, include_examples))
    model = model or os.environ.get("PIPEVERIFY_LLM_MODEL")
    if provider == "anthropic":
        try:
            import anthropic
        except ImportError as exc:
            raise ExplainError("Install the AI extra: pip install 'pipeverify[ai]'") from exc
        client = anthropic.Anthropic()
        msg = client.messages.create(model=model or "claude-sonnet-5-5", max_tokens=400,
                                     messages=[{"role": "user", "content": prompt}])
        return "".join(getattr(b, "text", "") for b in msg.content).strip()
    if provider == "openai":
        try:
            import openai
        except ImportError as exc:
            raise ExplainError("Install the AI extra: pip install 'pipeverify[ai]'") from exc
        if not model:
            raise ExplainError("Set PIPEVERIFY_LLM_MODEL to the OpenAI model you want to use")
        client = openai.OpenAI()
        resp = client.chat.completions.create(model=model,
                                              messages=[{"role": "user", "content": prompt}])
        return (resp.choices[0].message.content or "").strip()
    raise ExplainError(f"Unknown provider '{provider}'. Use anthropic or openai.")
