"""End-to-end pipeline test: Claude Code -> proxy -> upstream -> back.

We stand up two ``ThreadingHTTPServer`` instances — one for the
``claude-oneclick`` proxy (the real code), one for a fake upstream that
mimics an OpenAI-compatible provider — and drive an Anthropic-shaped
request through the whole pipeline. This is what catches breakage at
the seams: request translation, header forwarding, response shape, SSE
streaming, tool calls, count_tokens, retry logic.
"""
from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests._helpers import isolated_home


# ---------- fake upstream (OpenAI-compatible) ------------------------------

class _UpstreamHandler(BaseHTTPRequestHandler):
    """Drop-in stand-in for DeepSeek/NIMs/etc."""

    # Class-level knobs the test sets per-case.
    response_payload: dict = {}
    stream_chunks: list[bytes] = []
    fail_first_n: int = 0
    fail_status: int = 500
    received: list = []

    def log_message(self, *a, **kw): return

    def do_GET(self):  # noqa: N802
        if self.path == "/v1/models":
            body = json.dumps({"data": [{"id": "test-model"}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        self.send_response(404); self.end_headers()

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}
        _UpstreamHandler.received.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": payload,
        })

        if self.path != "/v1/chat/completions":
            self.send_response(404); self.end_headers(); return

        # Inject failures for retry tests.
        if _UpstreamHandler.fail_first_n > 0:
            _UpstreamHandler.fail_first_n -= 1
            self.send_response(_UpstreamHandler.fail_status)
            self.send_header("Content-Type", "application/json")
            err = json.dumps({"error": {"message": "transient"}}).encode()
            self.send_header("Content-Length", str(len(err))); self.end_headers()
            self.wfile.write(err); return

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in _UpstreamHandler.stream_chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
            return

        body_out = json.dumps(_UpstreamHandler.response_payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)


def _free_port() -> int:
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


def _start_upstream() -> tuple[ThreadingHTTPServer, int]:
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _UpstreamHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _start_proxy(port: int) -> ThreadingHTTPServer:
    """Start the real proxy.serve in-process (NOT the daemon spawn).

    Pins the chosen port in config so the proxy's Host-header allowlist
    accepts requests on it (the allowlist reads from cfg.proxy.port).
    """
    from claude_oneclick.config import load, save
    from claude_oneclick.proxy import _Handler  # internal but stable
    cfg = load(); cfg.setdefault("proxy", {})["port"] = port; save(cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # Wait for socket.
    deadline = time.time() + 3
    import socket
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return srv
        except OSError:
            time.sleep(0.02)
    return srv


def _setup_preset(upstream_port: int, proxy_port: int | None = None, **overrides) -> None:
    """Make 'test-deepseek' the active preset, pointing at the local upstream.

    If ``proxy_port`` is given, also pin it in config so the proxy's
    Host-header allowlist (which reads from config) accepts requests on
    that port.
    """
    from claude_oneclick.config import load, save, set_active, set_enabled, upsert_user_preset
    p = {
        "name": "test-deepseek",
        "label": "Test",
        "base_url": f"http://127.0.0.1:{upstream_port}",
        "api_key": "sk-fake-test",
        "model": "test-model",
        "small_fast_model": "test-model",
        "format": "openai",
        "request_timeout_seconds": 10,
        "retries": 2,
        "retry_backoff": 1.05,
    }
    p.update(overrides)
    upsert_user_preset(p)
    set_active("test-deepseek")
    set_enabled(True)
    if proxy_port is not None:
        cfg = load(); cfg.setdefault("proxy", {})["port"] = proxy_port; save(cfg)


# ---------- tests ----------------------------------------------------------

class PipelineTests(unittest.TestCase):

    def setUp(self):
        _UpstreamHandler.received = []
        _UpstreamHandler.stream_chunks = []
        _UpstreamHandler.fail_first_n = 0

    def _post(self, proxy_port: int, body: dict, *, stream: bool = False) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/messages",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
            method="POST",
        )
        try:
            r = urllib.request.urlopen(req, timeout=8)
            return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers or {})

    def test_non_streaming_round_trip(self):
        with isolated_home():
            up, up_port = _start_upstream()
            try:
                _UpstreamHandler.response_payload = {
                    "id": "chatcmpl-1",
                    "model": "test-model",
                    "choices": [{
                        "message": {"role": "assistant", "content": "hello from the test"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                }
                _setup_preset(up_port)
                proxy_port = _free_port()
                _start_proxy(proxy_port)

                code, body, _ = self._post(proxy_port, {
                    "model": "claude-sonnet-4",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 50,
                })
                self.assertEqual(code, 200, body)
                resp = json.loads(body)
                self.assertEqual(resp["type"], "message")
                self.assertEqual(resp["content"][0]["text"], "hello from the test")
                self.assertEqual(resp["stop_reason"], "end_turn")
                self.assertEqual(resp["usage"]["input_tokens"], 7)

                # The upstream should have received the OpenAI-shape request.
                up_req = _UpstreamHandler.received[-1]["body"]
                self.assertEqual(up_req["model"], "test-model")
                self.assertEqual(up_req["messages"][0]["content"], "hi")
                # Auth header forwarded.
                self.assertEqual(_UpstreamHandler.received[-1]["auth"], "Bearer sk-fake-test")
            finally:
                up.shutdown(); up.server_close()

    def test_streaming_round_trip(self):
        with isolated_home():
            up, up_port = _start_upstream()
            try:
                _UpstreamHandler.stream_chunks = [
                    b'data: {"choices":[{"delta":{"content":"hello "}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n',
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                    b'data: [DONE]\n\n',
                ]
                _setup_preset(up_port)
                proxy_port = _free_port()
                _start_proxy(proxy_port)

                req = urllib.request.Request(
                    f"http://127.0.0.1:{proxy_port}/v1/messages",
                    data=json.dumps({
                        "model": "claude-sonnet-4",
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 50,
                        "stream": True,
                    }).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                r = urllib.request.urlopen(req, timeout=8)
                self.assertEqual(r.status, 200)
                self.assertIn("text/event-stream", r.headers.get("Content-Type", ""))
                raw = r.read().decode("utf-8")
                events = [line for line in raw.splitlines() if line.startswith("event:")]
                # The required Anthropic streaming lifecycle.
                self.assertIn("event: message_start", events)
                self.assertIn("event: content_block_start", events)
                self.assertIn("event: content_block_delta", events)
                self.assertIn("event: content_block_stop", events)
                self.assertIn("event: message_delta", events)
                self.assertIn("event: message_stop", events)
                # Concatenate the text deltas.
                deltas = []
                for line in raw.splitlines():
                    if line.startswith("data: "):
                        try:
                            obj = json.loads(line[len("data: "):])
                        except Exception:
                            continue
                        if obj.get("type") == "content_block_delta":
                            deltas.append(obj["delta"].get("text", ""))
                self.assertEqual("".join(deltas), "hello world")
            finally:
                up.shutdown(); up.server_close()

    def test_tool_use_round_trip(self):
        with isolated_home():
            up, up_port = _start_upstream()
            try:
                _UpstreamHandler.response_payload = {
                    "id": "chatcmpl-tool",
                    "model": "test-model",
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call_42",
                                "type": "function",
                                "function": {"name": "add", "arguments": '{"a":1,"b":2}'},
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
                _setup_preset(up_port)
                proxy_port = _free_port()
                _start_proxy(proxy_port)

                code, body, _ = self._post(proxy_port, {
                    "model": "claude-sonnet-4",
                    "messages": [{"role": "user", "content": "what is 1+2?"}],
                    "max_tokens": 50,
                    "tools": [{
                        "name": "add",
                        "description": "add two numbers",
                        "input_schema": {"type": "object", "properties": {
                            "a": {"type": "number"}, "b": {"type": "number"}
                        }},
                    }],
                    "tool_choice": {"type": "auto"},
                })
                self.assertEqual(code, 200, body)
                resp = json.loads(body)
                # Anthropic-shape tool_use block in response.
                tool_block = next(b for b in resp["content"] if b.get("type") == "tool_use")
                self.assertEqual(tool_block["name"], "add")
                self.assertEqual(tool_block["input"], {"a": 1, "b": 2})
                self.assertEqual(resp["stop_reason"], "tool_use")
                # Outbound to upstream had OpenAI-shape tools.
                outbound = _UpstreamHandler.received[-1]["body"]
                self.assertEqual(outbound["tools"][0]["function"]["name"], "add")
                self.assertEqual(outbound["tool_choice"], "auto")
            finally:
                up.shutdown(); up.server_close()

    def test_count_tokens_endpoint(self):
        with isolated_home():
            _setup_preset(_free_port())  # upstream port doesn't matter for count_tokens
            proxy_port = _free_port()
            _start_proxy(proxy_port)
            req = urllib.request.Request(
                f"http://127.0.0.1:{proxy_port}/v1/messages/count_tokens",
                data=json.dumps({
                    "model": "claude-sonnet-4",
                    "messages": [{"role": "user", "content": "hello world this is some text"}],
                }).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            r = urllib.request.urlopen(req, timeout=4)
            self.assertEqual(r.status, 200)
            body = json.loads(r.read())
            self.assertIn("input_tokens", body)
            self.assertGreater(body["input_tokens"], 0)

    def test_retry_on_5xx(self):
        with isolated_home():
            up, up_port = _start_upstream()
            try:
                _UpstreamHandler.fail_first_n = 1
                _UpstreamHandler.fail_status = 503
                _UpstreamHandler.response_payload = {
                    "id": "x", "model": "test-model",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
                _setup_preset(up_port, retries=2, retry_backoff=1.0)
                proxy_port = _free_port()
                _start_proxy(proxy_port)

                code, body, _ = self._post(proxy_port, {
                    "model": "claude-sonnet-4",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 10,
                })
                self.assertEqual(code, 200, body)
                self.assertEqual(json.loads(body)["content"][0]["text"], "ok")
                # 1 failure + 1 success = 2 calls hit upstream
                self.assertEqual(len(_UpstreamHandler.received), 2)
            finally:
                up.shutdown(); up.server_close()

    def test_retry_on_429(self):
        with isolated_home():
            up, up_port = _start_upstream()
            try:
                _UpstreamHandler.fail_first_n = 1
                _UpstreamHandler.fail_status = 429
                _UpstreamHandler.response_payload = {
                    "id": "x", "model": "test-model",
                    "choices": [{"message": {"role": "assistant", "content": "rate-ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
                _setup_preset(up_port, retries=2, retry_backoff=1.0)
                proxy_port = _free_port()
                _start_proxy(proxy_port)

                code, body, _ = self._post(proxy_port, {
                    "model": "claude-sonnet-4",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 10,
                })
                self.assertEqual(code, 200, body)
                self.assertEqual(json.loads(body)["content"][0]["text"], "rate-ok")
            finally:
                up.shutdown(); up.server_close()

    def test_models_passthrough(self):
        with isolated_home():
            up, up_port = _start_upstream()
            try:
                _setup_preset(up_port)
                proxy_port = _free_port()
                _start_proxy(proxy_port)
                r = urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/v1/models", timeout=4)
                self.assertEqual(r.status, 200)
                data = json.loads(r.read())
                self.assertIn("data", data)
                self.assertEqual(data["data"][0]["id"], "test-model")
            finally:
                up.shutdown(); up.server_close()


if __name__ == "__main__":
    unittest.main()
