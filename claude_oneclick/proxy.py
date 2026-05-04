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

from claude_oneclick.config import get_preset, load
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

def _flatten_anthropic_content(content: Any) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Anthropic content can be a string or a list of blocks. Return:

    - text: concatenated text blocks
    - tool_uses: list of OpenAI ``tool_calls`` entries (assistant tool use)
    - tool_results: list of OpenAI ``role=tool`` messages (user tool result)
    """
    if content is None:
        return "", [], []
    if isinstance(content, str):
        return content, [], []
    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text") or "")
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
            if isinstance(inner, list):
                inner_text = "".join(
                    b.get("text", "") for b in inner if isinstance(b, dict) and b.get("type") == "text"
                )
            elif isinstance(inner, str):
                inner_text = inner
            else:
                inner_text = json.dumps(inner) if inner is not None else ""
            tool_results.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id") or "",
                "content": inner_text,
            })
        elif btype == "image":
            # Best-effort: most OpenAI providers accept image_url content
            # parts. Pass them through.
            src = block.get("source") or {}
            if src.get("type") == "base64":
                url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                text_parts.append(f"[image: {url[:30]}…]")
    return "\n".join(p for p in text_parts if p), tool_uses, tool_results


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

    for msg in req.get("messages") or []:
        role = msg.get("role")
        text, tool_uses, tool_results = _flatten_anthropic_content(msg.get("content"))
        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_uses:
                entry["tool_calls"] = tool_uses
            out_messages.append(entry)
        elif role == "user":
            # Anthropic packs `tool_result` blocks into the user turn; OpenAI
            # wants them as separate role=tool messages.
            if tool_results:
                out_messages.extend(tool_results)
            if text:
                out_messages.append({"role": "user", "content": text})
        else:
            out_messages.append({"role": role or "user", "content": text})

    # Resolve model with aliases.
    model = req.get("model") or preset.get("model") or ""
    aliases = preset.get("model_aliases") or {}
    if model in aliases:
        model = aliases[model]
    if not model:
        model = preset.get("model") or ""

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
    if "max_tokens" not in out:
        out["max_tokens"] = 4096

    # Stop sequences passthrough.
    stop = req.get("stop_sequences")
    if stop:
        out["stop"] = stop

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
    if preset.get("reasoning_enabled"):
        out["reasoning_effort"] = effort

    # extra_body merge — applies on every request, regardless of
    # reasoning_enabled (so users can pin fields like `safe_mode: true`).
    extra = preset.get("extra_body") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            out[k] = _substitute_effort(v, effort) if preset.get("reasoning_enabled") else v

    # Tool definitions.
    if req.get("tools"):
        out["tools"] = []
        for t in req["tools"]:
            out["tools"].append({
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description") or "",
                    "parameters": t.get("input_schema") or {"type": "object"},
                },
            })
        tc = req.get("tool_choice")
        if isinstance(tc, dict):
            ttype = tc.get("type")
            if ttype == "auto":
                out["tool_choice"] = "auto"
            elif ttype == "any":
                out["tool_choice"] = "required"
            elif ttype == "tool" and tc.get("name"):
                out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}

    return out


# ---------- non-streaming response translation ------------------------------

def openai_to_anthropic_response(resp: dict[str, Any], req_model: str) -> dict[str, Any]:
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content_blocks: list[dict[str, Any]] = []
    text = msg.get("content")
    if isinstance(text, str) and text:
        content_blocks.append({"type": "text", "text": text})
    elif isinstance(text, list):
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text":
                content_blocks.append({"type": "text", "text": part.get("text") or ""})
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

        # text delta
        text = delta.get("content")
        if isinstance(text, str) and text:
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
                "delta": {"type": "text_delta", "text": text},
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
        if self.text_block_open and self.text_index is not None:
            yield _sse_event("content_block_stop", {
                "type": "content_block_stop",
                "index": self.text_index,
            })
            self.text_block_open = False
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
        yield _sse_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": self.output_tokens},
        })
        yield _sse_event("message_stop", {"type": "message_stop"})


# ---------- HTTP handler ----------------------------------------------------

_MAX_BODY_BYTES = 32 * 1024 * 1024  # 32 MiB safety cap on any single request


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
        self._send_json(404, {"error": {"type": "not_found", "message": self.path}})

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": {"type": "host_not_allowed"}})
            return
        if self.path.startswith("/v1/messages"):
            self._handle_messages()
            return
        self._send_json(404, {"error": {"type": "not_found", "message": self.path}})

    # -- /v1/models passthrough ---------------------------------------------

    def _proxy_models(self) -> None:
        cfg = load()
        active = cfg.get("active") or "anthropic"
        preset = get_preset(active, cfg) or {}
        base = (preset.get("base_url") or "").rstrip("/")
        if not base:
            self._send_json(400, {"error": {"message": "no active preset / base_url"}})
            return
        url = f"{base}/v1/models"
        headers = {"Accept": "application/json"}
        api_key = preset.get("api_key") or ""
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

    # -- /v1/messages -------------------------------------------------------

    def _handle_messages(self) -> None:
        log = logging.getLogger("claude_oneclick.proxy")
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
        preset = get_preset(active, cfg) or {}
        if (preset.get("format") or "openai").lower() != "openai":
            self._send_json(400, {"error": {"message": "active preset is not OpenAI-format; the proxy is only used for openai presets"}})
            return
        base = (preset.get("base_url") or "").rstrip("/")
        api_key = preset.get("api_key") or ""
        if not base or not api_key:
            self._send_json(400, {"error": {"message": "active preset missing base_url or api_key"}})
            return

        wants_stream = bool(req.get("stream")) and not preset.get("disable_streaming")
        oai_req = anthropic_to_openai_request(req, preset)
        oai_req["stream"] = wants_stream
        if wants_stream:
            oai_req.setdefault("stream_options", {"include_usage": True})

        body = json.dumps(oai_req).encode("utf-8")
        url = f"{base}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if wants_stream else "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        for k, v in (preset.get("extra_headers") or {}).items():
            if isinstance(v, str):
                headers[k] = v

        timeout = float(preset.get("request_timeout_seconds") or 600)
        retries = int(preset.get("retries") or 0)
        backoff = float(preset.get("retry_backoff") or 1.5)

        log.info("→ upstream %s model=%s stream=%s", url, oai_req.get("model"), wants_stream)

        attempt = 0
        while True:
            try:
                up_req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                up = urllib.request.urlopen(up_req, timeout=timeout)
                break
            except urllib.error.HTTPError as e:
                err_body = b""
                try:
                    err_body = e.read()
                except Exception:
                    pass
                if 500 <= e.code < 600 and attempt < retries:
                    time.sleep(backoff ** attempt)
                    attempt += 1
                    continue
                self._send_json(e.code, {"type": "error", "error": {"type": "upstream_error", "message": err_body.decode("utf-8", "replace")[:1000]}})
                return
            except Exception as e:
                if attempt < retries:
                    time.sleep(backoff ** attempt)
                    attempt += 1
                    continue
                self._send_json(502, {"type": "error", "error": {"type": "upstream_error", "message": str(e)}})
                return

        if wants_stream:
            self._stream_back(up, oai_req["model"])
        else:
            try:
                raw_resp = up.read()
                resp_obj = json.loads(raw_resp.decode("utf-8"))
            except Exception as e:
                self._send_json(502, {"error": {"message": f"bad upstream JSON: {e}"}})
                return
            anth = openai_to_anthropic_response(resp_obj, oai_req["model"])
            self._send_json(200, anth)

    def _stream_back(self, up: Any, model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        translator = _StreamTranslator(model)
        for chunk_bytes in translator.start():
            self.wfile.write(chunk_bytes)
            self.wfile.flush()

        buf = b""
        try:
            while True:
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
