"""Built-in presets for popular Claude Code-compatible providers.

Each preset has these display-relevant fields:

    name        short id used by `claude-oneclick use <name>`
    label       the prominent first line in the UI (e.g. "DeepSeek V4 Pro")
    subtitle    secondary line (e.g. "Larger reasoning model", "via NVIDIA NIMs")
    tags        small capability badges (e.g. ["reasoning", "fast"])
    base_url    upstream API base
    api_key     literal API key (set by user via UI/CLI)
    model       model id passed as ANTHROPIC_MODEL
    small_fast_model  model id passed as ANTHROPIC_SMALL_FAST_MODEL
    format      "openai" — go through translation proxy
                "anthropic" — talk to upstream directly
    notes       freeform string shown on the detail page
    builtin     True for the presets shipped in this file
"""
from __future__ import annotations

from typing import Any


BUILTIN_PRESETS: list[dict[str, Any]] = [
    # --- DeepSeek (newest first; V4 family at the top) ---
    {
        "name": "deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "subtitle": "Larger reasoning model",
        "tags": ["reasoning"],
        "group": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "pricing": "paid",
        "model": "deepseek-v4-pro",
        "small_fast_model": "deepseek-v4-flash",
        "format": "openai",
        "notes": "DeepSeek V4 Pro — the larger reasoning model. The reasoning "
                 "slider on the Advanced tab controls extended thinking depth.",
        "reasoning_enabled": True,
        "builtin": True,
    },
    {
        "name": "deepseek-v4-flash",
        "label": "DeepSeek V4 Flash",
        "subtitle": "Smaller, faster reasoning model",
        "tags": ["reasoning", "fast"],
        "group": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "pricing": "paid",
        "model": "deepseek-v4-flash",
        "small_fast_model": "deepseek-v4-flash",
        "format": "openai",
        "notes": "DeepSeek V4 Flash — smaller and faster than Pro. Good "
                 "single-model choice on tight latency budgets.",
        "reasoning_enabled": True,
        "builtin": True,
    },
    {
        "name": "deepseek-chat",
        "label": "DeepSeek Chat",
        "subtitle": "V3 stable alias — auto-points at DeepSeek's latest chat model",
        "tags": [],
        "group": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "pricing": "paid",
        "model": "deepseek-chat",
        "small_fast_model": "deepseek-chat",
        "format": "openai",
        "notes": "Stable alias DeepSeek points at their latest non-reasoning "
                 "chat model. Click 'Refresh model list' to see what's live.",
        "builtin": True,
    },
    {
        "name": "deepseek-r1",
        "label": "DeepSeek R1",
        "subtitle": "Original DeepSeek reasoner",
        "tags": ["reasoning"],
        "group": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "pricing": "paid",
        "model": "deepseek-reasoner",
        "small_fast_model": "deepseek-chat",
        "format": "openai",
        "notes": "DeepSeek R1 (reasoner) for the main slot, V3 chat for "
                 "small/fast.",
        "reasoning_enabled": True,
        "builtin": True,
    },

    # --- NVIDIA NIMs ---
    # A few popular shortcuts hardcoded so users see something familiar
    # without entering an API key first. Everything else (and any model
    # NVIDIA adds in the future — DeepSeek V4 Pro/Flash variants, new
    # GLM revisions, Phi-N, etc.) shows up via the "Browse live catalog"
    # button in the sidebar, which calls /v1/models against
    # integrate.api.nvidia.com. So the list is always current without
    # me having to chase the catalog.
    {
        "name": "nim-llama-405b",
        "label": "Llama 3.1 405B",
        "subtitle": "via NVIDIA NIMs",
        "group": "NVIDIA NIMs",
        "tags": [],
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "pricing": "free-tier",
        "model": "meta/llama-3.1-405b-instruct",
        "small_fast_model": "meta/llama-3.1-8b-instruct",
        "format": "openai",
        "notes": "NVIDIA-hosted Llama 3.1 405B. Get a key at build.nvidia.com.",
        "builtin": True,
    },
    {
        "name": "nim-nemotron-70b",
        "label": "Nemotron 70B",
        "subtitle": "via NVIDIA NIMs",
        "group": "NVIDIA NIMs",
        "tags": [],
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "pricing": "free-tier",
        "model": "nvidia/llama-3.1-nemotron-70b-instruct",
        "small_fast_model": "meta/llama-3.1-8b-instruct",
        "format": "openai",
        "notes": "NVIDIA Nemotron-tuned Llama 70B.",
        "builtin": True,
    },
    {
        "name": "nim-deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "subtitle": "via NVIDIA NIMs — 1M-context, paid tier",
        "group": "NVIDIA NIMs",
        "tags": ["reasoning"],
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "pricing": "paid",
        "model": "deepseek-ai/deepseek-v4-pro",
        "small_fast_model": "meta/llama-3.1-8b-instruct",
        "format": "openai",
        "notes": "DeepSeek V4 Pro on NVIDIA NIMs — the larger 1M-context "
                 "reasoning model. NVIDIA bills this on the paid tier.",
        "reasoning_enabled": True,
        "builtin": True,
    },
    {
        "name": "nim-deepseek-v4-flash",
        "label": "DeepSeek V4 Flash",
        "subtitle": "via NVIDIA NIMs — fast, paid tier",
        "group": "NVIDIA NIMs",
        "tags": ["reasoning", "fast"],
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "pricing": "paid",
        "model": "deepseek-ai/deepseek-v4-flash",
        "small_fast_model": "meta/llama-3.1-8b-instruct",
        "format": "openai",
        "notes": "DeepSeek V4 Flash on NVIDIA NIMs — 284B MoE, 1M context, "
                 "tuned for fast coding/agent loops. Paid tier.",
        "reasoning_enabled": True,
        "builtin": True,
    },
    {
        "name": "nim-deepseek-v3",
        "label": "DeepSeek V3",
        "subtitle": "via NVIDIA NIMs — free tier",
        "group": "NVIDIA NIMs",
        "tags": [],
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "pricing": "free-tier",
        "model": "deepseek-ai/deepseek-v3",
        "small_fast_model": "meta/llama-3.1-8b-instruct",
        "format": "openai",
        "notes": "DeepSeek V3 on NVIDIA NIMs — free tier with the standard "
                 "build.nvidia.com credits.",
        "builtin": True,
    },
    {
        "name": "nim-glm-45",
        "label": "GLM 4.5",
        "subtitle": "via NVIDIA NIMs — Zhipu's GLM",
        "group": "NVIDIA NIMs",
        "tags": [],
        "base_url": "https://integrate.api.nvidia.com",
        "api_key": "",
        "pricing": "free-tier",
        "model": "zhipuai/glm-4.5",
        "small_fast_model": "meta/llama-3.1-8b-instruct",
        "format": "openai",
        "notes": "GLM 4.5 hosted on NVIDIA NIMs. The exact upstream id "
                 "varies — click 'Browse live catalog' on the sidebar or "
                 "'Refresh' on the Models tab to pick the right one.",
        "builtin": True,
    },

    # --- Other hosted providers ---
    {
        "name": "groq",
        "label": "Groq",
        "subtitle": "Llama 3.3 70B — very fast inference",
        "tags": ["fast"],
        "group": "Other hosted",
        "base_url": "https://api.groq.com/openai",
        "api_key": "",
        "pricing": "free-tier",
        "model": "llama-3.3-70b-versatile",
        "small_fast_model": "llama-3.1-8b-instant",
        "format": "openai",
        "notes": "Very fast inference; smaller context windows.",
        "builtin": True,
    },
    {
        "name": "together",
        "label": "Together AI",
        "subtitle": "DeepSeek V3, Llama, Mixtral",
        "tags": [],
        "group": "Other hosted",
        "base_url": "https://api.together.xyz",
        "api_key": "",
        "pricing": "paid",
        "model": "deepseek-ai/DeepSeek-V3",
        "small_fast_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "format": "openai",
        "notes": "Together AI hosted open models.",
        "builtin": True,
    },
    {
        "name": "fireworks",
        "label": "Fireworks AI",
        "subtitle": "DeepSeek V3 + open Llama variants",
        "tags": [],
        "group": "Other hosted",
        "base_url": "https://api.fireworks.ai/inference",
        "api_key": "",
        "pricing": "paid",
        "model": "accounts/fireworks/models/deepseek-v3",
        "small_fast_model": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "format": "openai",
        "notes": "Fireworks-hosted open models.",
        "builtin": True,
    },
    {
        "name": "openrouter",
        "label": "OpenRouter",
        "subtitle": "Multi-provider router (anthropic models passthrough)",
        "tags": [],
        "group": "Other hosted",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "pricing": "varies",
        "model": "anthropic/claude-sonnet-4",
        "small_fast_model": "anthropic/claude-haiku-4.5",
        "format": "openai",
        "notes": "OpenRouter routes to many providers; uses OpenAI format.",
        "builtin": True,
    },
    {
        "name": "ollama",
        "label": "Ollama",
        "subtitle": "Runs locally — no API key needed",
        "tags": ["local"],
        "group": "Local",
        "base_url": "http://localhost:11434",
        "api_key": "ollama",
        "pricing": "free",
        "model": "llama3.1:70b",
        "small_fast_model": "llama3.1:8b",
        "format": "openai",
        "notes": "Talks to a local Ollama instance. No real API key needed.",
        "builtin": True,
    },

    # --- The "no-op" preset (always last) ---
    {
        "name": "anthropic",
        "label": "Anthropic (default)",
        "subtitle": "Skip the proxy — talk to api.anthropic.com directly",
        "tags": [],
        "group": "Default",
        "base_url": "",
        "api_key": "",
        "model": "",
        "small_fast_model": "",
        "format": "anthropic",
        "notes": "The 'no-op' preset. Turning ON with this active is the same as OFF.",
        "builtin": True,
    },
]


def builtin_by_name(name: str) -> dict[str, Any] | None:
    for p in BUILTIN_PRESETS:
        if p["name"] == name:
            return dict(p)
    return None


def builtin_names() -> list[str]:
    return [p["name"] for p in BUILTIN_PRESETS]
