"""One call signature, two providers.

Each pass picks its own model, so pass one can run on Gemini Flash while
pass two runs on Sonnet, or both on either. Set it in .env:

    JOBSCOUT_PASS1=gemini:gemini-flash-latest
    JOBSCOUT_PASS2=anthropic:claude-sonnet-4-6

Gemini goes through Google's OpenAI-compatible endpoint, which means the
same request shape works for anything else that speaks it later:
OpenRouter, Together, Fireworks, a locally hosted Qwen.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import requests

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
ANTHROPIC_BASE = "https://api.anthropic.com/v1/messages"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def cost(self, rates: tuple[float, float]) -> float:
        return self.input_tokens / 1e6 * rates[0] + self.output_tokens / 1e6 * rates[1]


RATES = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gemini-flash-latest": (0.75, 3.75),
}


class LLMError(Exception):
    pass


def _post(url: str, headers: dict, payload: dict, attempts: int = 3) -> dict:
    for i in range(attempts):
        r = requests.post(url, headers=headers, json=payload, timeout=120)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 529) and i < attempts - 1:
            time.sleep(2 ** i * 3)      # free-tier rate limits are common
            continue
        raise LLMError(f"{r.status_code}: {r.text[:400]}")
    raise LLMError("exhausted retries")


def complete(spec: str, prompt: str, max_tokens: int = 3000) -> tuple[str, Usage]:
    """spec is 'provider:model'. Returns the text and token usage."""
    provider, _, model = spec.partition(":")

    if provider == "anthropic":
        data = _post(
            ANTHROPIC_BASE,
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
             "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": model, "max_tokens": max_tokens,
             "messages": [{"role": "user", "content": prompt}]},
        )
        text = "".join(b.get("text", "") for b in data["content"])
        u = data.get("usage", {})
        return text, Usage(u.get("input_tokens", 0), u.get("output_tokens", 0))

    if provider == "gemini":
        data = _post(
            GEMINI_BASE,
            {"Authorization": f"Bearer {os.environ['GOOGLE_API_KEY']}",
             "Content-Type": "application/json"},
            {"model": model, "max_tokens": max_tokens,
             "messages": [{"role": "user", "content": prompt}]},
        )
        text = data["choices"][0]["message"]["content"]
        u = data.get("usage", {})
        return text, Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))

    raise LLMError(f"Unknown provider {provider!r}. Use anthropic: or gemini:")


def parse_json(text: str) -> dict | list:
    """Models wrap JSON in fences and prose despite instructions."""
    text = text.strip()
    if "```" in text:
        block = text.split("```")[1]
        text = block[4:].strip() if block.lower().startswith("json") else block.strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=0)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])
