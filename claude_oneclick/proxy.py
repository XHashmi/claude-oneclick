"""Anthropic ↔ OpenAI translation proxy.

Listens on ``127.0.0.1:<proxy.port>`` and exposes Anthropic's
``POST /v1/messages`` endpoint. Translates the request into OpenAI's
``POST /v1/chat/completions`` shape, forwards it to the active preset's
upstream, and translates the response (incl. SSE streaming and tool calls)
back into Anthropic events. Claude Code never knows it isn't talking to
api.anthropic.com.

Started/stopped via ``ensure_running()`` / ``stop()`` — the actual server
runs in a detached child process so it survives ``claude-oneclick``
exiting.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

from claude_oneclick.config import _resolve_api_key, get_preset, load
from claude_oneclick.paths import config_dir, ensure_dirs, pid_file, proxy_log


# ---------- logging ---------------------------------------------------------

def _setup_logging() -> logging.Logger:
    cfg = load()
    level = logging.DEBUG if cfg.get("log_level") == "debug" else logging.INFO
    log = logging.getLogger("claude_oneclick.proxy")
    log.setLevel(level)
    if not log.handlers:
        ensure_dirs()
        fh = logging.FileHandler(proxy_log())
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    return log


def _join_endpoint(base: str, endpoint: str) -> str:
    """Append an endpoint path to a base URL, tolerating either convention.

    Many providers' docs publish ``https://host/api/v1`` as the base
    (OpenAI SDK convention), others publish ``https://host`` and expect
    you to add ``/v1/...`` yourself. We accept either: if the base
    already ends in ``/v1``, we don't add it again.
    """
    base = base.rstrip("/")
    endpoint = endpoint.lstrip("/")
    if base.endswith("/v1"):
        # endpoint may itself start with v1/... — strip that to avoid
        # producing a doubled segment (`v1/v1/chat/completions`).
        if endpoint.startswith("v1/"):
            endpoint = endpoint[len("v1/"):]
        return f"{base}/{endpoint}"
    if not endpoint.startswith("v1/"):
        endpoint = f"v1/{endpoint}"
    return f"{base}/{endpoint}"


def _typed_tool_to_function(t: dict[str, Any]) -> dict[str, Any] | None:
    """Translate Anthropic's server-typed tools into OpenAI function shape.

    Anthropic's typed tools (``bash_20241022``, ``text_editor_20241022``,
    ``computer_20241022``, ``web_search_20250305``) ship without an
    ``input_schema`` because the schema is implicit in the model's
    training. Third-party models don't have that training, so we
    synthesize a JSON Schema that matches the documented behavior.
    Returns None if the type is unrecognized — caller falls back to
    the generic empty-object shape.
    """
    ttype = (t.get("type") or "").lower()
    name = t.get("name") or ttype.split("_")[0]

    if ttype.startswith("bash"):
        return {"type": "function", "function": {
            "name": name,
            "description": "Run a bash command on the user's machine. Returns stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Bash command to execute."},
                    "restart": {"type": "boolean", "description": "Restart the bash session.", "default": False},
                },
                "required": ["command"],
            },
        }}

    if ttype.startswith("text_editor") or ttype.startswith("str_replace"):
        return {"type": "function", "function": {
            "name": name,
            "description": (
                "Filesystem text editor. Use 'view' to read, 'create' to write a "
                "new file, 'str_replace' to replace exact text, 'insert' to add a "
                "line at a position, 'undo_edit' to revert."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "enum": ["view", "create", "str_replace", "insert", "undo_edit"]},
                    "path": {"type": "string", "description": "Absolute path to the file."},
                    "file_text": {"type": "string", "description": "For create — full contents."},
                    "old_str": {"type": "string", "description": "For str_replace — exact existing text."},
                    "new_str": {"type": "string", "description": "For str_replace/insert — replacement text."},
                    "insert_line": {"type": "integer", "description": "For insert — line number after which to insert (0 = top)."},
                    "view_range": {"type": "array", "items": {"type": "integer"}, "description": "For view — [start, end] line range."},
                },
                "required": ["command", "path"],
            },
        }}

    if ttype.startswith("computer"):
        return {"type": "function", "function": {
            "name": name,
            "description": (
                "Control the user's screen and keyboard. Actions include "
                "screenshot, mouse_move, left_click, right_click, double_click, "
                "type (text), key (key combo), scroll, cursor_position."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "screenshot", "mouse_move", "left_click", "right_click",
                            "middle_click", "double_click", "triple_click", "left_click_drag",
                            "type", "key", "scroll", "cursor_position", "wait", "hold_key",
                        ],
                    },
                    "coordinate": {"type": "array", "items": {"type": "integer"}, "description": "[x, y] for mouse actions."},
                    "text": {"type": "string", "description": "For 'type' or 'key'."},
                    "scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "scroll_amount": {"type": "integer", "description": "Number of click-equivalents to scroll."},
                    "duration": {"type": "number", "description": "Seconds — for 'wait' / 'hold_key'."},
                },
                "required": ["action"],
            },
        }}

    if ttype.startswith("web_search"):
        return {"type": "function", "function": {
            "name": name,
            "description": "Search the web for the given query and return relevant results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                    "max_uses": {"type": "integer", "description": "Maximum number of searches.", "default": 5},
                },
                "required": ["query"],
            },
        }}

    if ttype.startswith("web_fetch"):
        return {"type": "function", "function": {
            "name": name,
            "description": "Fetch the content of a web page at the given URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch."},
                },
                "required": ["url"],
            },
        }}

    return None


def _read_response_bounded(up: Any, *, max_bytes: int = 32 * 1024 * 1024) -> bytes:
    """Read a non-streaming HTTP response body without hanging on keep-alive.

    Plain ``up.read()`` reads until upstream EOF. Modern HTTP/1.1 servers
    typically use keep-alive — they send the body, then DON'T close the
    socket, expecting the client to read up to Content-Length and reuse
    the connection. ``read()`` with no length argument doesn't know to
    stop, so it sits waiting for the upstream's TCP idle timeout
    (30-300s). During that wait, the proxy's response to Claude Code
    has already been written (the body is in our buffer) but the
    handler hasn't returned, so the connection stays open from Claude
    Code's perspective and the spinner sits there indefinitely.

    Strategy:
      1. If the upstream sent ``Content-Length``, read exactly that
         many bytes — bounded.
      2. Otherwise read in chunks until empty or until ``max_bytes``,
         whichever comes first.

    The 32 MiB ceiling matches our request-body cap and protects
    against a malicious upstream sending an unbounded reply.
    """
    cl = None
    try:
        cl_header = up.headers.get("Content-Length")
        if cl_header is not None:
            cl = int(cl_header)
    except (AttributeError, ValueError, TypeError):
        cl = None
    if cl is not None and 0 <= cl <= max_bytes:
        return up.read(cl)
    chunks: list[bytes] = []
    total = 0
    while total < max_bytes:
        chunk = up.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def _strip_think_tags(text: str) -> tuple[str, str]:
    """Pull out inline <think>...</think> reasoning blocks.

    Returns (text_with_tags_removed, concatenated_reasoning). Older
    DeepSeek-R1, QwQ, GLM-Z1, and Qwen3-thinking emit chain-of-thought
    inline like:

        <think>Let me work this out step by step...</think>The answer is 42.

    Claude Code can't render <think> tags meaningfully — left in, the
    user sees raw markup; the model's actual answer might be
    overshadowed. We split them so the proxy can route the answer to
    text content and the reasoning to a thinking-style fallback.
    """
    if not text or "<think>" not in text.lower():
        return text, ""
    reasoning_parts: list[str] = []
    def _capture(m: re.Match) -> str:
        reasoning_parts.append(m.group(1).strip())
        return ""
    cleaned = _THINK_RE.sub(_capture, text).strip()
    return cleaned, "\n\n".join(reasoning_parts).strip()



# ---------- request translation ---------------------------------------------


def _substitute_effort(v: Any, effort: str) -> Any:
    """Recursively replace the literal string ``{{effort}}`` with the slider
    value (``"low"|"medium"|"high"``). Walks dicts and lists. Used by
    ``extra_body`` so a single template entry like
    ``{"thinking": {"effort": "{{effort}}"}}`` adapts to the active level.
    """
    if isinstance(v, str):
        return v.replace("{{effort}}", effort)
    if isinstance(v, dict):
        return {k: _substitute_effort(vv, effort) for k, vv in v.items()}
    if isinstance(v, list):
        return [_substitute_effort(vv, effort) for vv in v]
    return v

def _flatten_anthropic_content(content: Any) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Anthropic content can be a string or a list of blocks. Return:

    - text: concatenated text blocks
    - tool_uses: list of OpenAI ``tool_calls`` entries (assistant tool use)
    - tool_results: list of OpenAI ``role=tool`` messages (user tool result)
    - images: list of OpenAI ``image_url`` content parts (vision passthrough)

    Anthropic's prompt-cache ``cache_control`` markers are silently
    flattened — most OpenAI-compatible providers 400 on the unknown
    field. Users who want cache passthrough can opt in via the preset's
    ``prompt_cache_passthrough`` flag.

    ``tool_result`` blocks marked ``is_error: true`` are prefixed with
    a ``[tool error]`` sentinel so the upstream model sees the failure
    (OpenAI's ``role=tool`` message has no native error flag).
    """
    if content is None:
        return "", [], [], []
    if isinstance(content, str):
        return content, [], [], []
    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text") or "")
        elif btype == "thinking":
            # Claude Code re-sends thinking blocks from previous turns
            # in conversation history. The upstream models we proxy to
            # don't speak Anthropic's thinking-block protocol, but
            # silently dropping the thought wastes useful context. Pass
            # it through as text under a marker so the next-turn model
            # can still see what the assistant was thinking about.
            t = block.get("thinking") or ""
            if t:
                text_parts.append(f"[previous thinking]\n{t}")
        elif btype == "tool_use":
            tool_uses.append({
                "id": block.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": block.get("name") or "",
                    "arguments": json.dumps(block.get("input") or {}),
                },
            })
        elif btype == "tool_result":
            inner = block.get("content")
            inner_text_parts: list[str] = []
            inner_images: list[dict[str, Any]] = []
            if isinstance(inner, list):
                for b in inner:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        inner_text_parts.append(b.get("text") or "")
                    elif b.get("type") == "image":
                        # Tool result with an image (e.g. screenshot from
                        # a computer-use tool). OpenAI's role=tool
                        # message can't carry images directly, so we
                        # surface them as a follow-up user-role image
                        # part the model will see right after the tool
                        # text. Anthropic's tool_result already mixes
                        # text + image so the model trains on this pattern.
                        src = b.get("source") or {}
                        if src.get("type") == "base64":
                            url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                            inner_images.append({"type": "image_url", "image_url": {"url": url}})
                        elif src.get("type") == "url" and src.get("url"):
                            inner_images.append({"type": "image_url", "image_url": {"url": src["url"]}})
                inner_text = "".join(inner_text_parts)
            elif isinstance(inner, str):
                inner_text = inner
            else:
                inner_text = json.dumps(inner) if inner is not None else ""
            if block.get("is_error"):
                inner_text = f"[tool error] {inner_text}".rstrip()
            # Extend the tool message itself with the image; the
            # caller-side message construction lifts these into a user
            # role+image follow-up message for vision-capable upstreams.
            if inner_images:
                images.extend(inner_images)
            tool_results.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id") or "",
                "content": inner_text,
            })
        elif btype == "image":
            # Real OpenAI vision passthrough. Anthropic spec:
            #   {"type": "image", "source": {"type": "base64",
            #    "media_type": "image/png", "data": "..."}}
            #   or {"type": "image", "source": {"type": "url", "url": "..."}}
            # OpenAI spec:
            #   {"type": "image_url", "image_url": {"url": "..."}}
            src = block.get("source") or {}
            if src.get("type") == "base64":
                url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                images.append({"type": "image_url", "image_url": {"url": url}})
            elif src.get("type") == "url" and src.get("url"):
                images.append({"type": "image_url", "image_url": {"url": src["url"]}})
    return "\n".join(p for p in text_parts if p), tool_uses, tool_results, images


def anthropic_to_openai_request(req: dict[str, Any], preset: dict[str, Any]) -> dict[str, Any]:
    """Convert an Anthropic /v1/messages payload to OpenAI /v1/chat/completions."""
    out_messages: list[dict[str, Any]] = []

    # System prompt: Anthropic ships it as a top-level field (string OR list
    # of content blocks). OpenAI puts it as a role=system message.
    sys_text = ""
    sysv = req.get("system")
    if isinstance(sysv, str):
        sys_text = sysv
    elif isinstance(sysv, list):
        sys_text = "\n".join(
            b.get("text", "") for b in sysv if isinstance(b, dict) and b.get("type") == "text"
        )
    prefix = preset.get("system_prompt_prefix") or ""
    suffix = preset.get("system_prompt_suffix") or ""
    sys_text = (prefix + ("\n" if prefix and sys_text else "") + sys_text +
                ("\n" if suffix and sys_text else "") + suffix).strip()
    if sys_text:
        out_messages.append({"role": "system", "content": sys_text})

    disable_vision = bool(preset.get("disable_vision"))
    for msg in req.get("messages") or []:
        role = msg.get("role")
        text, tool_uses, tool_results, images = _flatten_anthropic_content(msg.get("content"))
        if disable_vision:
            images = []
        # Build the OpenAI `content` field: when there are images,
        # use the multipart array; otherwise keep a flat string for
        # back-compat with non-vision providers that 400 on arrays.
        def _build_content(t: str, imgs: list[dict[str, Any]]) -> Any:
            if imgs:
                parts: list[dict[str, Any]] = []
                if t:
                    parts.append({"type": "text", "text": t})
                parts.extend(imgs)
                return parts
            return t
        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": _build_content(text, []) or None}
            if tool_uses:
                entry["tool_calls"] = tool_uses
            out_messages.append(entry)
        elif role == "user":
            # Anthropic packs `tool_result` blocks into the user turn; OpenAI
            # wants them as separate role=tool messages.
            if tool_results:
                out_messages.extend(tool_results)
            if text or images:
                out_messages.append({"role": "user", "content": _build_content(text, images)})
        else:
            out_messages.append({"role": role or "user", "content": _build_content(text, images)})

    # Resolve the upstream model id.
    #
    # Precedence (most specific first):
    #   1. If the request's model is in `model_aliases`, use the mapped value.
    #   2. **Otherwise the preset's configured model wins.** Claude Code
    #      can request specific models for subagents, compaction, memory
    #      updates, or via /model, and those ids may or may not exist on
    #      the user's chosen provider. If the user picked "DeepSeek V4
    #      Flash" they want V4 Flash for every call — including the
    #      background ones — full stop. Letting Claude Code's
    #      hard-coded Haiku id (or a leftover V4 Pro id from a previous
    #      session) leak through silently splits the user's actual cost
    #      and behavior across two unrelated models.
    req_model = req.get("model") or ""
    aliases = preset.get("model_aliases") or {}
    if req_model in aliases:
        model = aliases[req_model]
    else:
        model = preset.get("model") or req_model

    out: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "stream": bool(req.get("stream")),
    }

    # Sampling — request values win, then preset defaults.
    samp = preset.get("sampling") or {}
    for k_ant, k_oai in (("max_tokens", "max_tokens"), ("temperature", "temperature"),
                         ("top_p", "top_p"), ("top_k", "top_k")):
        v = req.get(k_ant)
        if v is None:
            v = samp.get(k_ant)
        if v is not None and k_oai not in ("top_k",):  # OpenAI lacks top_k
            out[k_oai] = v
    # "No output cap" mode — preset-pinned. Claude Code always sends
    # max_tokens (Anthropic requires it), but most OpenAI-style upstreams
    # cut off generations at whatever cap was sent. Setting an
    # astronomically large value lets the model run until its OWN
    # internal ceiling (every current model has one well below this),
    # while still keeping an upper bound so a misbehaving model can't
    # produce an infinite stream. Stripping max_tokens entirely was
    # tempting, but in practice some chat models then ramble forever
    # without ever emitting their EOT token.
    if preset.get("no_output_cap"):
        out["max_tokens"] = 65536
    elif "max_tokens" not in out:
        out["max_tokens"] = 4096

    # Extended OpenAI-standard sampling — preset-only (Claude Code never
    # sends these on /v1/messages). Skip silently if the upstream doesn't
    # recognize them; OpenAI ignores unknowns rather than 400ing.
    for k in ("frequency_penalty", "presence_penalty", "seed"):
        v = samp.get(k)
        if v is not None:
            out[k] = v

    # Stop sequences — preset-pinned overrides request, since the user
    # explicitly configured stop tokens for this preset's model.
    stop = samp.get("stop") or req.get("stop_sequences")
    if stop:
        out["stop"] = stop

    # response_format — passthrough JSON mode for upstreams that honor
    # OpenAI's text/json_object/json_schema convention. Preset-pinned.
    rf = preset.get("response_format")
    if rf:
        if isinstance(rf, str) and rf in ("text", "json_object"):
            out["response_format"] = {"type": rf}
        elif isinstance(rf, dict):
            out["response_format"] = rf

    # Reasoning toggle.
    #
    # Two channels, both ON when `reasoning_enabled` is true:
    #
    #   1. The OpenAI-standard `reasoning_effort: "low"|"medium"|"high"`
    #      field. Honored by OpenAI o-series, DeepSeek V4 Pro/Flash, and
    #      any upstream that follows the OpenAI convention. Ignored by
    #      everyone else.
    #
    #   2. Whatever the user put in `extra_body`. That dict is merged
    #      into the outbound request as-is, with one substitution: any
    #      string value equal to `{{effort}}` is replaced with the
    #      slider's current value. This lets a single preset target
    #      non-standard reasoning fields like `{"reasoning": true}`,
    #      `{"thinking": {"budget_tokens": 4096}}`, vendor-specific
    #      enable flags, etc.
    effort = preset.get("reasoning_effort") or "medium"
    if effort not in ("low", "medium", "high"):
        effort = "medium"
    # Claude Code has its own reasoning controls (settings.json
    # "thinking" config). When the user turns reasoning on/off there,
    # the request includes a `thinking` field that we honor BEFORE
    # falling back to the preset's reasoning_enabled flag. This way:
    #   - Claude Code OFF + preset OFF  → no reasoning
    #   - Claude Code OFF + preset ON   → preset's reasoning_effort fires
    #     (legacy behavior — preset can still force it on)
    #   - Claude Code ON  + preset *    → Claude Code's thinking config
    #     wins; we map its budget_tokens / type into reasoning_effort
    #     and pass thinking={type:enabled} into extra_body for upstreams
    #     that prefer the Anthropic-style field.
    cc_thinking = req.get("thinking") or {}
    if isinstance(cc_thinking, dict) and cc_thinking.get("type") == "enabled":
        # Map budget_tokens to a coarse effort level.
        budget = int(cc_thinking.get("budget_tokens") or 0)
        if budget <= 2048:
            mapped_effort = "low"
        elif budget <= 8192:
            mapped_effort = "medium"
        else:
            mapped_effort = "high"
        out["reasoning_effort"] = mapped_effort
        effort = mapped_effort
        # Pass an Anthropic-style `thinking` block through so upstreams
        # that read it honor it directly. DeepSeek's late-2025 docs
        # specifically expect ``{type, reasoning_effort: "high"|"max"}``
        # — we emit BOTH shapes (budget + their effort enum) so the
        # native DeepSeek API and other upstreams that take budget_tokens
        # both work.
        ds_effort = "max" if mapped_effort == "high" else "high"
        out["thinking"] = {
            "type": "enabled",
            "budget_tokens": budget or 4096,
            "reasoning_effort": ds_effort,
        }
    elif preset.get("reasoning_enabled"):
        out["reasoning_effort"] = effort

    # extra_body merge — applies on every request, regardless of
    # reasoning_enabled (so users can pin fields like `safe_mode: true`).
    extra = preset.get("extra_body") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            out[k] = _substitute_effort(v, effort) if preset.get("reasoning_enabled") else v

    # Tool definitions. Anthropic's API supports two shapes:
    #   1. Standard custom tools — {name, description, input_schema}.
    #      Pass straight through as OpenAI-style functions.
    #   2. Server-typed tools — {type: "bash_20241022"|"text_editor_*"|
    #      "computer_*"|"web_search_*"|...}. These have implicit
    #      schemas that only Anthropic's models know. Third-party
    #      models need an explicit JSON Schema or they emit garbage,
    #      so we synthesize one based on the type. Same name maps to
    #      the same function on the model side, so tool_use blocks
    #      round-trip cleanly back to Claude Code.
    if req.get("tools"):
        out["tools"] = []
        for t in req["tools"]:
            tdef = _typed_tool_to_function(t) if (t.get("type") and not t.get("input_schema")) else None
            if tdef is None:
                tdef = {
                    "type": "function",
                    "function": {
                        "name": t.get("name"),
                        "description": t.get("description") or "",
                        "parameters": t.get("input_schema") or {"type": "object"},
                    },
                }
            out["tools"].append(tdef)
        tc = req.get("tool_choice")
        if isinstance(tc, dict):
            ttype = tc.get("type")
            if ttype == "auto":
                out["tool_choice"] = "auto"
            elif ttype == "any":
                out["tool_choice"] = "required"
            elif ttype == "tool" and tc.get("name"):
                out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
                # JSON-mode bridge: if the user named a tool whose name
                # matches the preset's `json_mode_tool_name` (default
                # "json_response"), also flip OpenAI's response_format
                # so providers that don't grok function-as-json-enforcer
                # still emit JSON.
                json_tool = preset.get("json_mode_tool_name") or "json_response"
                if tc["name"] == json_tool:
                    out["response_format"] = {"type": "json_object"}
            elif ttype == "none":
                out["tool_choice"] = "none"

        # Anthropic's `disable_parallel_tool_use: true` -> OpenAI's
        # `parallel_tool_calls: false`.
        if req.get("disable_parallel_tool_use") is True:
            out["parallel_tool_calls"] = False

    # metadata passthrough — Anthropic and OpenAI both accept a top-level
    # metadata object (typically {user_id: ...}). Most third-party
    # providers ignore it, which is fine — but for those that DO log it
    # for audit/billing (OpenAI, some self-hosted gateways), losing it
    # makes attribution impossible.
    if req.get("metadata"):
        out["metadata"] = req["metadata"]

    return out


# ---------- non-streaming response translation ------------------------------

def openai_to_anthropic_response(resp: dict[str, Any], req_model: str) -> dict[str, Any]:
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content_blocks: list[dict[str, Any]] = []
    text = msg.get("content")
    # Reasoning models emit chain-of-thought in either:
    #   * a separate `reasoning_content` field (DeepSeek V4, Groq), OR
    #   * inline `<think>...</think>` tags inside `content` (older
    #     DeepSeek-R1, QwQ, GLM-Z1, Qwen3-thinking).
    # If the model's whole budget went to reasoning and `content` is
    # empty, we'd emit zero text blocks and Claude Code would render
    # "No response requested". Use the reasoning as a fallback, after
    # stripping the inline tags.
    reasoning_text = msg.get("reasoning_content") or msg.get("reasoning") or ""
    if not reasoning_text:
        nvext = msg.get("nvext") or {}
        if isinstance(nvext, dict):
            reasoning_text = nvext.get("thinking") or nvext.get("reasoning_content") or ""
    if isinstance(text, str):
        cleaned, inline_reasoning = _strip_think_tags(text)
        if not reasoning_text and inline_reasoning:
            reasoning_text = inline_reasoning
        text = cleaned
    if isinstance(text, str) and text.strip():
        content_blocks.append({"type": "text", "text": text})
    elif isinstance(text, list):
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text":
                clean, inline = _strip_think_tags(part.get("text") or "")
                if clean.strip():
                    content_blocks.append({"type": "text", "text": clean})
                if not reasoning_text and inline:
                    reasoning_text = inline
    # If we have no visible content but DO have reasoning, surface it
    # so the user sees *something* instead of an empty turn. Wrap with
    # a clear marker so it doesn't look like the model's actual answer.
    if not content_blocks and reasoning_text and reasoning_text.strip():
        content_blocks.append({
            "type": "text",
            "text": (
                "[reasoning model used its entire output budget on "
                "internal chain-of-thought without producing a final "
                "answer — try increasing max_tokens, or pick a "
                "non-reasoning model variant]\n\n"
                f"<thinking>\n{reasoning_text.strip()}\n</thinking>"
            ),
        })
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {"_raw": fn.get("arguments")}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
            "name": fn.get("name") or "",
            "input": args,
        })
    finish = choice.get("finish_reason") or "stop"
    stop_reason = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence",
    }.get(finish, "end_turn")
    usage = resp.get("usage") or {}
    return {
        "id": resp.get("id") or f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": resp.get("model") or req_model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ---------- streaming translation -------------------------------------------

def _sse_event(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


class _StreamTranslator:
    """Holds state across an SSE stream from upstream → Claude Code.

    OpenAI's streaming chunks emit deltas under ``choices[0].delta``:
    either ``content`` (plain text) or ``tool_calls[i].function.{name,arguments}``.
    Anthropic's wire wants one ``content_block_*`` lifecycle per text or
    tool-use block. We track which blocks are currently open and synthesize
    ``content_block_start`` / ``content_block_stop`` accordingly.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.message_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.text_block_open = False
        self.tool_blocks: dict[int, str] = {}  # tool index → block id
        self.next_index = 0
        self.text_index: int | None = None
        self.tool_index_map: dict[int, int] = {}  # OpenAI tool idx → Anthropic block idx
        self.input_tokens = 0
        self.output_tokens = 0
        self.finish_reason: str | None = None
        self.started = False
        # Reasoning-model bookkeeping. We accumulate reasoning_content
        # deltas (separate field on DeepSeek V4 / Groq) AND strip
        # inline <think>...</think> tags from content (DeepSeek-R1,
        # QwQ, GLM-Z1, Qwen3-thinking). If by end-of-stream we got
        # reasoning but no real text, emit the reasoning so the user
        # sees something instead of "No response requested".
        self._reasoning_buf = ""
        self._real_text_emitted = False
        self._think_open = False  # tracking inline <think> across chunks
        self._content_carry = ""  # bytes from a half-tag, deferred
        self._thinking_block_open = False
        self._thinking_index: int | None = None
        # How to render reasoning to the client. Set by handle_chunk's
        # caller, since the translator itself doesn't have the preset.
        self.stream_reasoning_mode: str = "thinking_block"

    def start(self) -> Iterator[bytes]:
        self.started = True
        yield _sse_event("message_start", {
            "type": "message_start",
            "message": {
                "id": self.message_id,
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })
        # Anthropic's reference stream emits an early `ping` so any
        # buffering reverse proxy / IDE extension knows there's
        # activity. Cheap insurance against intermediate buffering
        # delays that can also keep Claude Code's spinner spinning.
        yield _sse_event("ping", {"type": "ping"})

    def handle_chunk(self, chunk: dict[str, Any]) -> Iterator[bytes]:
        if not self.started:
            yield from self.start()
        choices = chunk.get("choices") or []
        if not choices:
            usage = chunk.get("usage") or {}
            if usage:
                self.input_tokens = usage.get("prompt_tokens", self.input_tokens)
                self.output_tokens = usage.get("completion_tokens", self.output_tokens)
            return
        delta = choices[0].get("delta") or {}
        finish = choices[0].get("finish_reason")
        if finish:
            self.finish_reason = finish

        # reasoning_content delta (DeepSeek V4, Groq, etc.) — emit
        # LIVE as a Anthropic-style thinking block so the user sees
        # progress instead of a frozen spinner. Reasoning models can
        # spend 5-30s on private chain-of-thought before any visible
        # text appears; without this, Claude Code shows
        # "Deliberating..." for the whole duration with no feedback.
        # We also keep the buffered copy so finish() can fall back if
        # the real `content` never comes through.
        # Reasoning content can arrive under three keys depending on
        # provider:
        #   * ``reasoning_content`` — DeepSeek V4 native
        #   * ``reasoning`` — Groq, OpenRouter
        #   * ``nvext.thinking`` — NVIDIA NIMs vendor extension on
        #     reasoning-capable models (Nemotron-Super, DeepSeek-V4-Pro
        #     hosted via NIMs, etc.)
        rc = delta.get("reasoning_content") or delta.get("reasoning")
        if not rc:
            nvext = delta.get("nvext") or {}
            if isinstance(nvext, dict):
                rc = nvext.get("thinking") or nvext.get("reasoning_content")
        if isinstance(rc, str) and rc:
            self._reasoning_buf += rc
            mode = self.stream_reasoning_mode
            if mode == "hidden":
                pass  # silent buffering only
            elif mode == "text_prefix":
                # Universal fallback: emit reasoning as plain text with a
                # 🧠 prefix on the FIRST chunk so the user can see progress
                # even on clients that don't render thinking blocks.
                if not self.text_block_open:
                    self.text_index = self.next_index
                    self.next_index += 1
                    self.text_block_open = True
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start",
                        "index": self.text_index,
                        "content_block": {"type": "text", "text": ""},
                    })
                    rc = "🧠 " + rc
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self.text_index,
                    "delta": {"type": "text_delta", "text": rc},
                })
            else:  # "thinking_block" — Anthropic-shape, best UX where supported
                if not self._thinking_block_open and not self.text_block_open:
                    self._thinking_index = self.next_index
                    self.next_index += 1
                    self._thinking_block_open = True
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start",
                        "index": self._thinking_index,
                        "content_block": {"type": "thinking", "thinking": ""},
                    })
                if self._thinking_block_open:
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta",
                        "index": self._thinking_index,
                        "delta": {"type": "thinking_delta", "thinking": rc},
                    })

        # text delta — strip inline <think>...</think> tags across
        # chunk boundaries before emitting.
        text = delta.get("content")
        if isinstance(text, str) and text:
            visible = self._consume_content_chunk(text)
            if visible:
                self._real_text_emitted = True
                # Real text means reasoning is over — close the
                # thinking block before opening text. Anthropic's
                # spec wants a signature_delta before close so the
                # client doesn't hang waiting for cryptographic proof
                # of the thought (we use a stub since third-party
                # upstreams can't sign with Anthropic's keys).
                if self._thinking_block_open and self._thinking_index is not None:
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta",
                        "index": self._thinking_index,
                        "delta": {"type": "signature_delta", "signature": ""},
                    })
                    yield _sse_event("content_block_stop", {
                        "type": "content_block_stop",
                        "index": self._thinking_index,
                    })
                    self._thinking_block_open = False
                if not self.text_block_open:
                    self.text_index = self.next_index
                    self.next_index += 1
                    self.text_block_open = True
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start",
                        "index": self.text_index,
                        "content_block": {"type": "text", "text": ""},
                    })
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self.text_index,
                    "delta": {"type": "text_delta", "text": visible},
                })

        # tool-call deltas
        for tc in delta.get("tool_calls") or []:
            oai_idx = tc.get("index", 0)
            if oai_idx not in self.tool_index_map:
                # Close the text block before starting a tool block (Anthropic
                # wants strict block-at-a-time ordering).
                if self.text_block_open and self.text_index is not None:
                    yield _sse_event("content_block_stop", {
                        "type": "content_block_stop",
                        "index": self.text_index,
                    })
                    self.text_block_open = False
                idx = self.next_index
                self.next_index += 1
                self.tool_index_map[oai_idx] = idx
                fn = tc.get("function") or {}
                self.tool_blocks[idx] = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                yield _sse_event("content_block_start", {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": self.tool_blocks[idx],
                        "name": fn.get("name") or "",
                        "input": {},
                    },
                })
            idx = self.tool_index_map[oai_idx]
            fn = tc.get("function") or {}
            arg_delta = fn.get("arguments")
            if arg_delta:
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "input_json_delta", "partial_json": arg_delta},
                })

    def finish(self) -> Iterator[bytes]:
        if not self.started:
            yield from self.start()
        # Stream ended. Flush any deferred-from-end-of-chunk carry —
        # if it's not actually a `<think>` opener, it's just trailing
        # response text that was held back in case it was a partial
        # tag. Forgetting this dropped final words/punctuation.
        if self._content_carry and not self._content_carry.lower().startswith("<think"):
            tail = self._content_carry
            self._content_carry = ""
            self._real_text_emitted = True
            if not self.text_block_open:
                self.text_index = self.next_index
                self.next_index += 1
                self.text_block_open = True
                yield _sse_event("content_block_start", {
                    "type": "content_block_start",
                    "index": self.text_index,
                    "content_block": {"type": "text", "text": ""},
                })
            yield _sse_event("content_block_delta", {
                "type": "content_block_delta",
                "index": self.text_index,
                "delta": {"type": "text_delta", "text": tail},
            })
        # If the model produced ONLY chain-of-thought (reasoning_content
        # or unclosed <think>) and never emitted real text, surface the
        # reasoning so Claude Code shows *something* instead of "No
        # response requested".
        fallback = ""
        if not self._real_text_emitted:
            fallback = (self._reasoning_buf or self._content_carry).strip()
        if fallback and not self.text_block_open:
            self.text_index = self.next_index
            self.next_index += 1
            self.text_block_open = True
            yield _sse_event("content_block_start", {
                "type": "content_block_start",
                "index": self.text_index,
                "content_block": {"type": "text", "text": ""},
            })
            yield _sse_event("content_block_delta", {
                "type": "content_block_delta",
                "index": self.text_index,
                "delta": {
                    "type": "text_delta",
                    "text": (
                        "[reasoning model used its entire output budget on "
                        "internal chain-of-thought without producing a final "
                        "answer — try increasing max_tokens, or pick a "
                        "non-reasoning model variant]\n\n"
                        f"<thinking>\n{fallback}\n</thinking>"
                    ),
                },
            })
        if self.text_block_open and self.text_index is not None:
            yield _sse_event("content_block_stop", {
                "type": "content_block_stop",
                "index": self.text_index,
            })
            self.text_block_open = False
        if self._thinking_block_open and self._thinking_index is not None:
            yield _sse_event("content_block_delta", {
                "type": "content_block_delta",
                "index": self._thinking_index,
                "delta": {"type": "signature_delta", "signature": ""},
            })
            yield _sse_event("content_block_stop", {
                "type": "content_block_stop",
                "index": self._thinking_index,
            })
            self._thinking_block_open = False
        for idx in list(self.tool_index_map.values()):
            yield _sse_event("content_block_stop", {
                "type": "content_block_stop",
                "index": idx,
            })
        stop_reason = {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
            "content_filter": "stop_sequence",
            None: "end_turn",
        }.get(self.finish_reason, "end_turn")
        # Claude Code reads stop_reason out of message_delta (not
        # message_stop), and uses cumulative `usage` to mark the turn
        # complete. Some clients won't transition out of "thinking" if
        # input_tokens is missing, so always emit both.
        yield _sse_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
        })
        yield _sse_event("message_stop", {"type": "message_stop"})

    def _consume_content_chunk(self, text: str) -> str:
        """Feed a content delta through the inline <think>-stripping state
        machine. Returns the visible portion (everything outside think tags)
        and pushes any reasoning portion into ``_reasoning_buf``.

        Handles tags split across chunk boundaries: a "<think" arriving in
        chunk N and ">..." in chunk N+1 must still be recognized.
        """
        buf = self._content_carry + text
        self._content_carry = ""
        out = []
        i = 0
        while i < len(buf):
            if not self._think_open:
                # Look for an opening <think>.
                idx = buf.lower().find("<think>", i)
                if idx == -1:
                    # No tag start. But we might have a partial "<think"
                    # at the very end — defer up to 7 chars.
                    tail = buf[max(i, len(buf) - 7):].lower()
                    cut = len(buf)
                    for k in range(len(tail)):
                        if "<think>".startswith(tail[k:]):
                            cut = max(i, len(buf) - 7) + k
                            break
                    out.append(buf[i:cut])
                    self._content_carry = buf[cut:]
                    i = len(buf)
                    break
                out.append(buf[i:idx])
                self._think_open = True
                i = idx + len("<think>")
            else:
                end = buf.lower().find("</think>", i)
                if end == -1:
                    # Tag still open — consume rest as reasoning.
                    self._reasoning_buf += buf[i:]
                    i = len(buf)
                    break
                self._reasoning_buf += buf[i:end] + "\n\n"
                self._think_open = False
                i = end + len("</think>")
        return "".join(out)


# ---------- HTTP handler ----------------------------------------------------

_MAX_BODY_BYTES = 32 * 1024 * 1024  # 32 MiB safety cap on any single request


def _attempt_with_retries(url, body, headers, timeout, retries, backoff, oai_req, log):
    """Try POSTing to `url` with `body`/`headers`. Returns (response_or_None,
    status_or_None, err_text_or_None). On success, response is the urlopen
    handle; on terminal failure, response is None and the caller can decide
    what to do (e.g. try a fallback preset).

    Handles:
      - 5xx/429 retries with exponential backoff and Retry-After
      - the `stream_options.include_usage` 400 fallback (drops the option
        and retries once)
    """
    attempt = 0
    while True:
        try:
            up_req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            up = urllib.request.urlopen(up_req, timeout=timeout)
            return up, None, None
        except urllib.error.HTTPError as e:
            err_body = b""
            try:
                err_body = e.read()
            except Exception:
                pass
            err_text = err_body.decode("utf-8", "replace")[:1000]
            if e.code == 400 and "stream_options" in err_text and "stream_options" in oai_req:
                log.info("upstream rejected stream_options; retrying without it")
                oai_req.pop("stream_options", None)
                body = json.dumps(oai_req).encode("utf-8")
                continue
            retriable = (500 <= e.code < 600) or e.code == 429
            if retriable and attempt < retries:
                delay = backoff ** attempt
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra:
                    try:
                        delay = max(delay, float(ra))
                    except ValueError:
                        pass
                time.sleep(delay)
                attempt += 1
                continue
            return None, e.code, err_text
        except Exception as e:
            if attempt < retries:
                time.sleep(backoff ** attempt)
                attempt += 1
                continue
            return None, 502, str(e)


class _Handler(BaseHTTPRequestHandler):
    server_version = "claude-oneclick-proxy/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        logging.getLogger("claude_oneclick.proxy").info("%s - " + format, self.client_address[0], *args)

    def _host_ok(self) -> bool:
        """Reject foreign Host headers (DNS rebinding defense).

        Anyone who can DNS-rebind to 127.0.0.1 could otherwise have your
        browser drain your provider's API quota through this proxy.
        """
        cfg = load()
        host = (self.headers.get("Host") or "").lower().strip()
        port = int(cfg.get("proxy", {}).get("port", 47824))
        allowed = {
            f"127.0.0.1:{port}", "127.0.0.1",
            f"localhost:{port}", "localhost",
            f"[::1]:{port}", "[::1]",
        }
        return host in allowed

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": {"type": "host_not_allowed"}})
            return
        if self.path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if self.path.startswith("/v1/models"):
            self._proxy_models()
            return
        # Surface unknown GETs in the log so we can spot when Claude Code
        # is calling an endpoint we haven't implemented.
        log = logging.getLogger("claude_oneclick.proxy")
        log.warning("UNKNOWN GET endpoint: %s", self.path)
        self._send_json(404, {"error": {"type": "not_found", "message": self.path}})

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": {"type": "host_not_allowed"}})
            return
        # Routing: count_tokens BEFORE the generic /v1/messages handler,
        # because the latter would otherwise swallow it via startswith().
        if self.path.startswith("/v1/messages/count_tokens"):
            self._handle_count_tokens()
            return
        if self.path.startswith("/v1/messages"):
            self._handle_messages()
            return
        log = logging.getLogger("claude_oneclick.proxy")
        log.warning("UNKNOWN POST endpoint: %s", self.path)
        self._send_json(404, {"error": {"type": "not_found", "message": self.path}})

    # -- /v1/models passthrough ---------------------------------------------

    def _proxy_models(self) -> None:
        cfg = load()
        active = cfg.get("active") or "anthropic"
        override = self.headers.get("X-CoC-Preset")
        if override:
            active = override
        preset = get_preset(active, cfg) or {}
        base = (preset.get("base_url") or "").rstrip("/")
        if not base:
            self._send_json(400, {"error": {"message": "no active preset / base_url"}})
            return
        url = _join_endpoint(base, "models")
        headers = {"Accept": "application/json"}
        api_key = _resolve_api_key(preset)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                body = resp.read()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self._send_json(e.code, {"error": {"message": str(e)}})
        except Exception as e:
            self._send_json(502, {"error": {"message": str(e)}})

    # -- /v1/messages/count_tokens -----------------------------------------

    def _handle_count_tokens(self) -> None:
        """Anthropic's token-count endpoint, estimated locally.

        Most OpenAI-compatible providers don't expose a token-counting
        endpoint, so we approximate from the request body. Claude Code
        uses this to manage context windows; an over-estimate is safer
        than an under-estimate (which would let it pack the context too
        full and 400 on the real /v1/messages call).
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            req = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            req = {}
        # Roughly 1 token per 3.5 characters of English text — overestimates
        # for code (which is denser) but Claude Code itself uses similar
        # heuristics when an API call is unavailable.
        text_chars = 0

        def _walk(v: Any) -> None:
            nonlocal text_chars
            if isinstance(v, str):
                text_chars += len(v)
            elif isinstance(v, list):
                for x in v:
                    _walk(x)
            elif isinstance(v, dict):
                for x in v.values():
                    _walk(x)

        _walk(req.get("system"))
        _walk(req.get("messages"))
        _walk(req.get("tools"))
        approx = max(1, text_chars // 4)
        self._send_json(200, {"input_tokens": approx})

    # -- /v1/messages -------------------------------------------------------

    def _handle_messages(self) -> None:
        log = logging.getLogger("claude_oneclick.proxy")
        # User-facing label for the proxy log: lets users see "after
        # my message there were 4 follow-up requests, the 3rd one took
        # 47s" without having to dig through SSE chunks.
        log.info("inbound /v1/messages from %s (override=%s)",
                 self.client_address[0], self.headers.get("X-CoC-Preset"))
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > _MAX_BODY_BYTES:
                self._send_json(413, {"error": {"message": "request too large"}})
                return
            raw = self.rfile.read(length) if length else b""
            req = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:
            self._send_json(400, {"error": {"message": f"bad JSON: {e}"}})
            return

        cfg = load()
        active = cfg.get("active") or "anthropic"
        # Optional per-request override: a header (set by /api/test-preset
        # and any future "try this preset" UIs) can pin which preset to
        # use for THIS request only, without mutating global state.
        override = self.headers.get("X-CoC-Preset")
        if override:
            active = override
        preset = get_preset(active, cfg) or {}
        if (preset.get("format") or "openai").lower() != "openai":
            self._send_json(400, {"error": {"message": "active preset is not OpenAI-format; the proxy is only used for openai presets"}})
            return
        base = (preset.get("base_url") or "").rstrip("/")
        api_key = _resolve_api_key(preset)
        if not base or not api_key:
            self._send_json(400, {"error": {"message": f"preset {active!r} missing base_url or api_key"}})
            return

        wants_stream = bool(req.get("stream")) and not preset.get("disable_streaming")
        oai_req = anthropic_to_openai_request(req, preset)
        oai_req["stream"] = wants_stream
        if wants_stream:
            oai_req.setdefault("stream_options", {"include_usage": True})

        body = json.dumps(oai_req).encode("utf-8")
        url = _join_endpoint(base, "chat/completions")
        # One Idempotency-Key per inbound request, reused across retries
        # so a flaky network 5xx that the upstream actually completed
        # doesn't double-bill. OpenAI honors this; non-OpenAI providers
        # ignore it.
        idem = f"coc-{uuid.uuid4().hex}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if wants_stream else "application/json",
            "Authorization": f"Bearer {api_key}",
            "Idempotency-Key": idem,
        }
        for k, v in (preset.get("extra_headers") or {}).items():
            if isinstance(v, str):
                headers[k] = v
        # Anthropic beta headers (e.g. anthropic-beta:
        # prompt-caching-2024-07-31, message-batches-2024-09-24) are
        # only meaningful when we're talking to api.anthropic.com.
        # Forward them when the preset is Anthropic-format (the proxy
        # mostly isn't in that path, but a user may set up a custom
        # Anthropic-compatible upstream behind us). Drop on third-party
        # OpenAI-style providers — they 400 on unknown headers.
        upstream_is_anthropic = "anthropic.com" in (base or "").lower()
        if upstream_is_anthropic:
            for hk, hv in self.headers.items():
                if hk.lower().startswith("anthropic-") and hk not in headers:
                    headers[hk] = hv

        timeout = float(preset.get("request_timeout_seconds") or 600)
        retries = int(preset.get("retries") or 0)
        backoff = float(preset.get("retry_backoff") or 1.5)

        # If Claude Code asked for a different model than the preset
        # provides, log the override so the user can see it in the
        # diagnostic log. Useful for catching subagent/compaction model
        # leakage like the V4 Pro slipping through on a V4 Flash preset.
        client_model = req.get("model") or ""
        if client_model and client_model != oai_req.get("model"):
            log.info("model override: client asked for %r, sending %r per preset",
                     client_model, oai_req.get("model"))
        log.info("→ upstream %s model=%s stream=%s", url, oai_req.get("model"), wants_stream)

        up, fail_status, fail_text = _attempt_with_retries(
            url, body, headers, timeout, retries, backoff, oai_req, log,
        )
        if up is None:
            # Primary preset exhausted retries. If the user configured a
            # fallback, take exactly one hop to it. Never cascade.
            fb_name = preset.get("fallback")
            if fb_name and fb_name != active:
                fb_preset = get_preset(fb_name, cfg)
                if fb_preset and (fb_preset.get("format") or "openai").lower() == "openai":
                    log.info("primary failed (%s); falling back to preset %s", fail_status, fb_name)
                    fb_base = (fb_preset.get("base_url") or "").rstrip("/")
                    fb_key = _resolve_api_key(fb_preset)
                    if fb_base and fb_key:
                        # Re-translate using the fallback preset's settings
                        # (different model id, different headers).
                        fb_req = anthropic_to_openai_request(req, fb_preset)
                        fb_req["stream"] = wants_stream
                        if wants_stream:
                            fb_req.setdefault("stream_options", {"include_usage": True})
                        fb_body = json.dumps(fb_req).encode("utf-8")
                        fb_url = _join_endpoint(fb_base, "chat/completions")
                        fb_headers = {
                            "Content-Type": "application/json",
                            "Accept": "text/event-stream" if wants_stream else "application/json",
                            "Authorization": f"Bearer {fb_key}",
                            "Idempotency-Key": idem,
                        }
                        for k, v in (fb_preset.get("extra_headers") or {}).items():
                            if isinstance(v, str):
                                fb_headers[k] = v
                        up, _, _ = _attempt_with_retries(
                            fb_url, fb_body, fb_headers, timeout, 0, backoff, fb_req, log,
                        )
                        if up is not None:
                            oai_req = fb_req  # for the streamer below
            if up is None:
                self._send_json(fail_status or 502, {
                    "type": "error",
                    "error": {"type": "upstream_error", "message": fail_text or "upstream unreachable"},
                })
                return

        import time as _t
        t_start = _t.time()
        if wants_stream:
            self._stream_back(up, oai_req["model"], preset)
        else:
            try:
                raw_resp = _read_response_bounded(up)
                resp_obj = json.loads(raw_resp.decode("utf-8"))
            except Exception as e:
                self._send_json(502, {"error": {"message": f"bad upstream JSON: {e}"}})
                return
            finally:
                # Reclaim the upstream socket NOW. Without this, a
                # keep-alive connection sat idle waiting for a TCP
                # timeout (30-300s) and Claude Code's background call
                # spinner would hang for the same duration.
                try:
                    up.close()
                except Exception:
                    pass
            log.info("← upstream non-stream done in %.2fs (model=%s, bytes=%d)",
                     _t.time() - t_start, oai_req.get("model"), len(raw_resp))
            anth = openai_to_anthropic_response(resp_obj, oai_req["model"])
            # Record usage to the per-preset ledger.
            try:
                from claude_oneclick import usage as usage_mod
                u = anth.get("usage") or {}
                usage_mod.record(active,
                                 input_tokens=u.get("input_tokens", 0),
                                 output_tokens=u.get("output_tokens", 0))
            except Exception:
                pass
            self._send_json(200, anth)

    def _stream_back(self, up: Any, model: str, preset: dict | None = None) -> None:
        log = logging.getLogger("claude_oneclick.proxy")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # Defeat any reverse-proxy / VSCode-extension buffering. Without
        # this header some intermediaries hold the SSE response until
        # the connection closes, defeating the whole point of
        # streaming. Required to make message_stop arrive promptly at
        # Claude Code rather than at end-of-connection.
        self.send_header("X-Accel-Buffering", "no")
        # Explicitly close-on-end so Claude Code's HTTP client doesn't
        # try to reuse the connection while we're still draining the
        # upstream socket.
        self.send_header("Connection", "close")
        self.end_headers()
        translator = _StreamTranslator(model)
        if preset:
            mode = preset.get("stream_reasoning") or "thinking_block"
            if mode in ("thinking_block", "text_prefix", "hidden"):
                translator.stream_reasoning_mode = mode
        for chunk_bytes in translator.start():
            self.wfile.write(chunk_bytes)
            self.wfile.flush()

        buf = b""
        done = False
        try:
            while not done:
                line = up.readline()
                if not line:
                    break
                buf += line
                if not line.strip():
                    # End of an SSE event. Process the buffered "data:" lines.
                    for evline in buf.splitlines():
                        if not evline.startswith(b"data:"):
                            continue
                        payload = evline[len(b"data:"):].strip()
                        if not payload:
                            continue
                        if payload == b"[DONE]":
                            # Upstream is done. STOP reading — calling
                            # readline() again would block until upstream's
                            # TCP idle-timeout (30-60s on most providers),
                            # during which Claude Code keeps showing the
                            # spinner ("Puzzling…") even though the actual
                            # response was complete. Setting `done` exits
                            # the outer while so we run finish() and close
                            # our HTTP response immediately.
                            done = True
                            buf = b""
                            break
                        try:
                            chunk = json.loads(payload.decode("utf-8"))
                        except Exception:
                            continue
                        for out in translator.handle_chunk(chunk):
                            self.wfile.write(out)
                            self.wfile.flush()
                    buf = b""
        finally:
            for out in translator.finish():
                try:
                    self.wfile.write(out)
                    self.wfile.flush()
                except Exception:
                    break
            # Close the upstream socket so we don't leak file
            # descriptors when the client disconnects mid-stream
            # or when [DONE] arrives but upstream stays open.
            try:
                up.close()
            except Exception:
                pass
            log.info("← upstream stream closed (model=%s, real_text=%s, finish=%s)",
                     model, translator._real_text_emitted, translator.finish_reason)


# ---------- daemon control -------------------------------------------------

def _is_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _read_pid() -> int | None:
    try:
        return int(pid_file().read_text().strip())
    except Exception:
        return None


def _alive(pid: int) -> bool:
    try:
        if platform.system() == "Windows":
            # On Windows, signal 0 isn't reliable; use tasklist.
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
            return str(pid) in out.stdout
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_running() -> bool:
    pid = _read_pid()
    if pid and _alive(pid):
        return True
    cfg = load()
    p = cfg.get("proxy", {})
    return _is_listening(p.get("host", "127.0.0.1"), int(p.get("port", 47824)))


def stop() -> bool:
    pid = _read_pid()
    if pid and _alive(pid):
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
                # Give it a second to exit cleanly.
                for _ in range(10):
                    if not _alive(pid):
                        break
                    time.sleep(0.1)
                if _alive(pid):
                    os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    try:
        pid_file().unlink()
    except FileNotFoundError:
        pass
    return True


def ensure_running() -> bool:
    """Start the proxy in a detached child process if not already up."""
    if is_running():
        return True
    cfg = load()
    p = cfg.get("proxy", {})
    host = p.get("host", "127.0.0.1")
    port = int(p.get("port", 47824))

    log = proxy_log()
    ensure_dirs()
    # Detached child running `python -m claude_oneclick._proxyd <host> <port>`.
    args = [sys.executable, "-m", "claude_oneclick.proxy", "--serve", host, str(port)]
    if platform.system() == "Windows":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        proc = subprocess.Popen(
            args,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdout=open(log, "ab"),
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
    else:
        proc = subprocess.Popen(
            args,
            stdout=open(log, "ab"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    pid_file().write_text(str(proc.pid))
    # Wait briefly for the listen socket.
    for _ in range(40):
        if _is_listening(host, port):
            return True
        time.sleep(0.05)
    return _is_listening(host, port)


# ---------- entrypoint when started as `python -m claude_oneclick.proxy --serve` ---

def _serve(host: str, port: int) -> None:
    _setup_logging()
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    log = logging.getLogger("claude_oneclick.proxy")
    log.info("proxy listening on %s:%d", host, port)
    try:
        httpd.serve_forever()
    finally:
        log.info("proxy shutting down")


def _main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[1] == "--serve":
        _serve(argv[2], int(argv[3]))
        return 0
    print("usage: python -m claude_oneclick.proxy --serve HOST PORT", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
