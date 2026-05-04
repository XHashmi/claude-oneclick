"""Heuristic: does a given upstream model id likely support tool/function calls?

Claude Code's agent loop is built around tool use — Read, Edit, Bash, etc. A
model with no tool-call training will usually echo a JSON-ish answer instead
of emitting a real tool_calls block, which leads to "infinite thinking" and
empty responses. So we surface a small badge in the UI to set expectations.

Heuristic only — no probe call. We err toward "yes" for major instruct
families and toward "no" for known tool-incapable model lines (Llama 2,
deepseek-coder pre-V2.5, Phi <14B, etc.). Users can always disagree and
try a model anyway; the badge is a hint, not a hard gate.
"""
from __future__ import annotations

import re

# Substrings that, when present in the model id, indicate solid native
# tool/function-calling support. Lowercase comparison.
_KNOWN_GOOD_PATTERNS = (
    "claude",                # all Claude family
    "gpt-4", "gpt-5", "o1", "o3", "o4",
    "deepseek-v3", "deepseek-v4", "deepseek-chat", "deepseek-r1", "deepseek-reasoner",
    "llama-3.1", "llama-3.2", "llama-3.3", "llama-4", "llama3.1", "llama3.2", "llama3.3",
    "qwen2.5", "qwen-2.5", "qwen-3", "qwen3",
    "mistral-large", "mistral-small", "mistral-medium", "mixtral-8x22b", "mixtral-8x7b",
    "nemotron-70b", "nemotron-340b", "nemotron-super", "nemotron-ultra",
    "gemini-1.5", "gemini-2", "gemini-pro",
    "command-r", "command-a",
    "grok-2", "grok-3", "grok-4",
    "glm-4", "glm-4.5", "glm-4.6",
    "yi-large",
    "phi-4",  # Phi-4 14B does function calling; smaller Phi don't.
)

# Substrings that indicate the model almost certainly does NOT support
# real tool calls in a way Claude Code can drive. These take precedence
# over the good list so e.g. "llama-2" overrides any partial match.
_KNOWN_BAD_PATTERNS = (
    "llama-2", "llama2",
    "llama-3-8b", "llama-3-70b",  # base Llama-3 (pre-3.1) — function calling is hit-and-miss
    "code-llama", "codellama",
    "deepseek-coder-v1", "deepseek-llm",
    "phi-2", "phi-3-mini", "phi-3-small",
    "embedding", "embed-",
    "whisper", "tts", "dall-e", "stable-diffusion",
    "gemma-2b", "gemma-7b",  # smaller Gemma variants
    "vicuna", "alpaca", "wizardlm",
)


def supports_tools(model_id: str | None) -> bool | None:
    """Return True / False / None (unknown) for whether ``model_id`` is
    likely to drive Claude Code's tool loop.

    None means we don't have enough signal to guess — UI should show a
    neutral "?" rather than a confident yes/no.
    """
    if not model_id:
        return None
    m = model_id.lower()
    # Strip provider prefixes like "meta/" / "openai/" / "anthropic/".
    if "/" in m:
        m = m.split("/", 1)[1]
    # Known-bad first.
    for pat in _KNOWN_BAD_PATTERNS:
        if pat in m:
            return False
    # Known-good.
    for pat in _KNOWN_GOOD_PATTERNS:
        if pat in m:
            return True
    # Heuristic: anything explicitly tagged "instruct" or "chat" from a
    # mainstream lab is *probably* fine, but we don't want to overclaim,
    # so return None (unknown).
    if re.search(r"\binstruct\b|\bchat\b", m):
        return None
    return None
