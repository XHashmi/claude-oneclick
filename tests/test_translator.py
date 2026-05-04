import json
import unittest

from claude_oneclick.proxy import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    _StreamTranslator,
    _join_endpoint,
    _read_response_bounded,
)


PRESET = {
    "name": "test",
    "model": "deepseek-v4",
    "small_fast_model": "deepseek-chat",
    "model_aliases": {"claude-sonnet-4": "deepseek-v4"},
    "sampling": {"temperature": 0.5, "max_tokens": None},
    "system_prompt_prefix": "",
    "system_prompt_suffix": "",
}


class RequestTranslatorTests(unittest.TestCase):
    def test_basic_text_request(self):
        req = {
            "model": "deepseek-v4",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 100,
            "stream": False,
        }
        out = anthropic_to_openai_request(req, PRESET)
        self.assertEqual(out["model"], "deepseek-v4")
        self.assertEqual(out["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(out["max_tokens"], 100)
        self.assertFalse(out["stream"])

    def test_system_message_and_content_blocks(self):
        req = {
            "model": "deepseek-v4",
            "system": "you are helpful",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "yo"}]},
            ],
        }
        out = anthropic_to_openai_request(req, PRESET)
        self.assertEqual(out["messages"][0], {"role": "system", "content": "you are helpful"})
        self.assertEqual(out["messages"][1]["content"], "hi")
        self.assertEqual(out["messages"][2]["content"], "yo")

    def test_tool_use_round_trip_to_openai(self):
        req = {
            "model": "deepseek-v4",
            "messages": [
                {"role": "user", "content": "calc 2+2"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "add", "input": {"a": 2, "b": 2}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": [{"type": "text", "text": "4"}]},
                ]},
            ],
            "tools": [{"name": "add", "description": "add two numbers", "input_schema": {"type": "object"}}],
        }
        out = anthropic_to_openai_request(req, PRESET)
        # assistant message must have tool_calls
        assistant = next(m for m in out["messages"] if m["role"] == "assistant")
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "add")
        self.assertEqual(json.loads(assistant["tool_calls"][0]["function"]["arguments"]), {"a": 2, "b": 2})
        # tool result becomes role=tool with the same tool_call_id
        tool_msg = next(m for m in out["messages"] if m["role"] == "tool")
        self.assertEqual(tool_msg["tool_call_id"], "tu_1")
        self.assertEqual(tool_msg["content"], "4")
        # tools list translated
        self.assertEqual(out["tools"][0]["function"]["name"], "add")

    def test_model_alias(self):
        req = {"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, PRESET)
        self.assertEqual(out["model"], "deepseek-v4")

    def test_sampling_defaults_apply_when_missing(self):
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, PRESET)
        self.assertEqual(out["temperature"], 0.5)

    def test_extended_sampling_passthrough(self):
        preset = dict(PRESET, sampling={
            "temperature": 0.7,
            "frequency_penalty": 0.4,
            "presence_penalty": -0.2,
            "seed": 1234,
            "stop": ["</answer>", "###"],
        })
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, preset)
        self.assertEqual(out["frequency_penalty"], 0.4)
        self.assertEqual(out["presence_penalty"], -0.2)
        self.assertEqual(out["seed"], 1234)
        self.assertEqual(out["stop"], ["</answer>", "###"])

    def test_response_format_passthrough(self):
        preset = dict(PRESET, response_format="json_object")
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, preset)
        self.assertEqual(out["response_format"], {"type": "json_object"})

    def test_response_format_blank_omits_field(self):
        preset = dict(PRESET, response_format="")
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, preset)
        self.assertNotIn("response_format", out)

    def test_metadata_passthrough(self):
        preset = dict(PRESET)
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"user_id": "u-123"},
        }
        out = anthropic_to_openai_request(req, preset)
        self.assertEqual(out["metadata"], {"user_id": "u-123"})

    def test_metadata_omitted_when_absent(self):
        preset = dict(PRESET)
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, preset)
        self.assertNotIn("metadata", out)

    def test_tool_result_image_extracted_to_image_part(self):
        # When a tool_result includes an image (e.g. screenshot from
        # computer-use), it must reach a vision-capable model — not
        # silently dropped.
        from claude_oneclick.proxy import _flatten_anthropic_content
        text, tools, results, images = _flatten_anthropic_content([
            {"type": "tool_result", "tool_use_id": "tu_1", "content": [
                {"type": "text", "text": "screenshot taken"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QkFTRTY0"}},
            ]},
        ])
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(results[0]["content"], "screenshot taken")

    def test_history_thinking_blocks_become_text_marker(self):
        # Claude Code re-sends previous-turn thinking blocks in the
        # conversation history. The upstream model can't read Anthropic's
        # thinking-block protocol natively, but silently dropping them
        # wastes useful context — pass them through as marked text so
        # the next-turn model still sees what the assistant was thinking.
        preset = dict(PRESET)
        req = {
            "model": "x",
            "messages": [
                {"role": "user", "content": "what is 2+2?"},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "User wants arithmetic. 2+2=4."},
                    {"type": "text", "text": "4"},
                ]},
                {"role": "user", "content": "and 3+3?"},
            ],
        }
        out = anthropic_to_openai_request(req, preset)
        # Find the assistant message in the outbound payload.
        assistant_msg = next(m for m in out["messages"] if m["role"] == "assistant")
        # Should contain BOTH the marker and the visible answer.
        self.assertIn("[previous thinking]", assistant_msg["content"])
        self.assertIn("User wants arithmetic", assistant_msg["content"])
        self.assertIn("4", assistant_msg["content"])

    def test_anthropic_typed_tools_get_synthesized_schemas(self):
        # Anthropic's typed tools (bash_20241022, text_editor_*, etc.) ship
        # without an input_schema. Without translation, third-party models
        # see {"parameters": {"type": "object"}} and can't drive them.
        preset = dict(PRESET)
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": "list files"}],
            "tools": [
                {"type": "bash_20241022", "name": "bash"},
                {"type": "text_editor_20241022", "name": "str_replace_editor"},
                {"type": "web_search_20250305", "name": "web_search"},
                {"type": "computer_20241022", "name": "computer",
                 "display_width_px": 1024, "display_height_px": 768, "display_number": 1},
            ],
        }
        out = anthropic_to_openai_request(req, preset)
        names = {t["function"]["name"]: t["function"] for t in out["tools"]}
        self.assertIn("command", names["bash"]["parameters"]["properties"])
        self.assertIn("command", names["str_replace_editor"]["parameters"]["properties"])
        self.assertIn("query", names["web_search"]["parameters"]["properties"])
        self.assertIn("action", names["computer"]["parameters"]["properties"])

    def test_thinking_passthrough_overrides_preset(self):
        # Claude Code sends thinking={type:enabled, budget_tokens: 6000}
        # with reasoning ON in its own settings. The proxy should map this
        # to reasoning_effort=medium AND keep the thinking field for
        # upstreams that read it natively (DeepSeek, NIMs nvext.thinking).
        preset = dict(PRESET, reasoning_enabled=False)
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "thinking": {"type": "enabled", "budget_tokens": 6000},
        }
        out = anthropic_to_openai_request(req, preset)
        self.assertEqual(out["reasoning_effort"], "medium")
        # Includes both budget_tokens (Anthropic shape) and DeepSeek's
        # reasoning_effort enum so either upstream variant is honored.
        self.assertEqual(out["thinking"]["type"], "enabled")
        self.assertEqual(out["thinking"]["budget_tokens"], 6000)
        self.assertIn(out["thinking"]["reasoning_effort"], ("high", "max"))

    def test_no_output_cap_uses_high_ceiling(self):
        # With "No output cap" we override Claude Code's modest cap with
        # a value bigger than any current model's internal ceiling, so
        # the upstream stops at its OWN limit (and we still have an
        # upper bound to prevent runaway streams).
        preset = dict(PRESET, no_output_cap=True)
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 16384,
        }
        out = anthropic_to_openai_request(req, preset)
        self.assertEqual(out["max_tokens"], 65536)

    def test_max_tokens_default_when_cap_not_disabled(self):
        # Without no_output_cap and no explicit value anywhere, the
        # proxy still falls back to a sensible default.
        preset = dict(PRESET, no_output_cap=False, sampling={})
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, preset)
        self.assertEqual(out["max_tokens"], 4096)

    def test_reasoning_flag_passthrough(self):
        preset = dict(PRESET, reasoning_enabled=True, reasoning_effort="high")
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, preset)
        self.assertEqual(out["reasoning_effort"], "high")
        # When the toggle is off the field is absent.
        out2 = anthropic_to_openai_request(req, dict(PRESET, reasoning_enabled=False))
        self.assertNotIn("reasoning_effort", out2)
        # An invalid effort falls back to medium.
        out3 = anthropic_to_openai_request(
            req, dict(PRESET, reasoning_enabled=True, reasoning_effort="ultra")
        )
        self.assertEqual(out3["reasoning_effort"], "medium")

    def test_extra_body_merged_with_effort_substitution(self):
        # The {{effort}} placeholder must resolve recursively (nested dicts
        # and lists) when reasoning is ON.
        preset = dict(
            PRESET,
            reasoning_enabled=True, reasoning_effort="high",
            extra_body={
                "thinking": {"level": "{{effort}}"},
                "passthrough_flag": True,
                "tags": ["{{effort}}", "model-x"],
            },
        )
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, preset)
        self.assertEqual(out["thinking"], {"level": "high"})
        self.assertTrue(out["passthrough_flag"])
        self.assertEqual(out["tags"], ["high", "model-x"])

    def test_extra_body_no_substitution_when_reasoning_off(self):
        # When reasoning is OFF, extra_body still merges but {{effort}}
        # is NOT substituted (it stays literal so the user sees a clear
        # signal that their toggle is off).
        preset = dict(
            PRESET, reasoning_enabled=False,
            extra_body={"safe_mode": True, "level": "{{effort}}"},
        )
        req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = anthropic_to_openai_request(req, preset)
        self.assertTrue(out["safe_mode"])
        self.assertEqual(out["level"], "{{effort}}")


class ResponseTranslatorTests(unittest.TestCase):
    def test_basic_text_response(self):
        oai = {
            "id": "chatcmpl-1",
            "model": "deepseek-v4",
            "choices": [{
                "message": {"role": "assistant", "content": "hello world"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        }
        out = openai_to_anthropic_response(oai, "deepseek-v4")
        self.assertEqual(out["type"], "message")
        self.assertEqual(out["content"], [{"type": "text", "text": "hello world"}])
        self.assertEqual(out["stop_reason"], "end_turn")
        self.assertEqual(out["usage"]["input_tokens"], 10)

    def test_tool_call_response(self):
        oai = {
            "id": "chatcmpl-2",
            "model": "deepseek-v4",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a":1,"b":2}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5},
        }
        out = openai_to_anthropic_response(oai, "deepseek-v4")
        self.assertEqual(out["stop_reason"], "tool_use")
        block = out["content"][0]
        self.assertEqual(block["type"], "tool_use")
        self.assertEqual(block["name"], "add")
        self.assertEqual(block["input"], {"a": 1, "b": 2})

    def test_inline_think_tags_stripped_from_content(self):
        # QwQ / older DeepSeek-R1 / GLM-Z1 emit reasoning inline.
        oai = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "<think>Let me work this out... 2+2 is 4.</think>The answer is 4.",
                },
                "finish_reason": "stop",
            }],
        }
        out = openai_to_anthropic_response(oai, "qwq")
        self.assertEqual(len(out["content"]), 1)
        self.assertEqual(out["content"][0]["text"], "The answer is 4.")

    def test_reasoning_content_used_when_visible_text_empty(self):
        # DeepSeek V4 with max_tokens too small: reasoning_content is
        # populated but content is empty. Without a fallback Claude Code
        # would render "No response requested".
        oai = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "Let me think about this problem...",
                },
                "finish_reason": "length",
            }],
        }
        out = openai_to_anthropic_response(oai, "deepseek-v4-flash")
        self.assertEqual(len(out["content"]), 1)
        self.assertIn("Let me think", out["content"][0]["text"])
        self.assertIn("max_tokens", out["content"][0]["text"])  # the hint
        self.assertEqual(out["stop_reason"], "max_tokens")

    def test_reasoning_content_ignored_when_real_content_present(self):
        oai = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello!",
                    "reasoning_content": "User said hi, I should greet back.",
                },
                "finish_reason": "stop",
            }],
        }
        out = openai_to_anthropic_response(oai, "deepseek-v4-flash")
        self.assertEqual(len(out["content"]), 1)
        self.assertEqual(out["content"][0]["text"], "Hello!")


class StreamTranslatorTests(unittest.TestCase):
    def _events(self, frames: list[dict]) -> list[dict]:
        t = _StreamTranslator("deepseek-v4")
        out: list[dict] = []
        for line in t.start():
            out.append(_decode(line))
        for f in frames:
            for line in t.handle_chunk(f):
                out.append(_decode(line))
        for line in t.finish():
            out.append(_decode(line))
        return out

    def test_text_stream_emits_lifecycle(self):
        events = self._events([
            {"choices": [{"delta": {"content": "hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "message_start")
        # An optional ping may appear right after message_start (matches
        # Anthropic's reference stream).
        self.assertEqual(types[1], "ping")
        self.assertIn("content_block_start", types)
        self.assertIn("content_block_delta", types)
        self.assertIn("content_block_stop", types)
        self.assertEqual(types[-2], "message_delta")
        self.assertEqual(types[-1], "message_stop")

    def test_tool_call_stream(self):
        events = self._events([
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "call_x", "function": {"name": "do_it", "arguments": ""},
            }]}}]},
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0, "function": {"arguments": "{\"q\":1}"},
            }]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ])
        starts = [e for e in events if e["type"] == "content_block_start"]
        self.assertEqual(starts[0]["content_block"]["type"], "tool_use")
        self.assertEqual(starts[0]["content_block"]["name"], "do_it")
        # input_json_delta carries argument fragments
        deltas = [e for e in events if e["type"] == "content_block_delta"]
        self.assertTrue(any(d["delta"].get("type") == "input_json_delta" for d in deltas))

    def test_trailing_chars_dont_get_swallowed_by_think_carry(self):
        # The <think>-stripping state machine defers the last few chars
        # of each chunk in case they're a partial "<think". If the
        # stream ends WITHOUT a real <think tag forming, those chars
        # must still be flushed — otherwise the user loses the end of
        # the response (e.g. a trailing "<" from "</answer>").
        events = self._events([
            {"choices": [{"delta": {"content": "Hi! How can I help"}}]},
            {"choices": [{"delta": {"content": " you today? <"}}]},  # ends with "<"
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])
        # Reassemble all text deltas the user would have seen.
        text = "".join(
            e["delta"]["text"] for e in events
            if e["type"] == "content_block_delta"
            and e["delta"].get("type") == "text_delta"
        )
        self.assertIn("Hi! How can I help you today?", text)
        self.assertTrue(text.rstrip().endswith("<"))  # the "<" wasn't lost


def _decode(raw: bytes) -> dict:
    # raw is `event: NAME\ndata: {...}\n\n`
    text = raw.decode("utf-8")
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    return {}


class VisionAndToolEdgeTests(unittest.TestCase):
    def test_image_block_becomes_image_url_part(self):
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "describe this:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
            ]}],
        }
        out = anthropic_to_openai_request(req, PRESET)
        msg = out["messages"][-1]
        self.assertIsInstance(msg["content"], list)
        self.assertEqual(msg["content"][0], {"type": "text", "text": "describe this:"})
        img = msg["content"][1]
        self.assertEqual(img["type"], "image_url")
        self.assertTrue(img["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_image_url_block_passes_through(self):
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "url", "url": "https://e.example/cat.jpg"}},
            ]}],
        }
        out = anthropic_to_openai_request(req, PRESET)
        msg = out["messages"][-1]
        self.assertEqual(msg["content"][0], {
            "type": "image_url", "image_url": {"url": "https://e.example/cat.jpg"},
        })

    def test_disable_vision_drops_images(self):
        # When the preset opts out of vision, image blocks are silently
        # dropped (don't poison the request for non-vision providers).
        preset = dict(PRESET, disable_vision=True)
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "hi"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A"}},
            ]}],
        }
        out = anthropic_to_openai_request(req, preset)
        # Falls back to flat string content (no image part).
        self.assertEqual(out["messages"][-1]["content"], "hi")

    def test_tool_result_is_error_marker(self):
        req = {
            "model": "x",
            "messages": [
                {"role": "user", "content": "calc"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t_1", "name": "div", "input": {"a": 1, "b": 0}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t_1", "is_error": True,
                     "content": [{"type": "text", "text": "division by zero"}]},
                ]},
            ],
        }
        out = anthropic_to_openai_request(req, PRESET)
        tool_msg = next(m for m in out["messages"] if m["role"] == "tool")
        self.assertIn("[tool error]", tool_msg["content"])
        self.assertIn("division by zero", tool_msg["content"])

    def test_disable_parallel_tool_use_maps(self):
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "x", "input_schema": {}}],
            "disable_parallel_tool_use": True,
        }
        out = anthropic_to_openai_request(req, PRESET)
        self.assertEqual(out["parallel_tool_calls"], False)

    def test_json_mode_bridge_via_tool_choice(self):
        # tool_choice → tool with the configured json-mode tool name
        # should ALSO set response_format: json_object.
        req = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "json_response", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "json_response"},
        }
        out = anthropic_to_openai_request(req, PRESET)
        self.assertEqual(out["response_format"], {"type": "json_object"})


class JoinEndpointTests(unittest.TestCase):
    """Both URL conventions must produce one (and only one) `/v1/...`."""

    def test_base_without_v1(self):
        self.assertEqual(_join_endpoint("https://api.deepseek.com", "chat/completions"),
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(_join_endpoint("https://api.deepseek.com/", "models"),
                         "https://api.deepseek.com/v1/models")

    def test_base_with_v1(self):
        self.assertEqual(_join_endpoint("https://openrouter.ai/api/v1", "chat/completions"),
                         "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(_join_endpoint("https://api.together.xyz/v1/", "models"),
                         "https://api.together.xyz/v1/models")


class BoundedReadTests(unittest.TestCase):
    """Non-streaming response reads must NOT hang on a keep-alive socket.

    The bug: ``up.read()`` with no arg waits for upstream EOF. Modern
    HTTP/1.1 servers send the body but keep the connection open for
    reuse, so ``read()`` blocks until upstream's TCP idle timeout
    (30-300s) — which is exactly the "Clauding…" / "Puzzling…" spinner
    hang the user reported.
    """
    class _FakeUpstream:
        def __init__(self, body: bytes, content_length: int | None):
            self._body = body
            self._pos = 0
            self.headers = {"Content-Length": str(content_length)} if content_length is not None else {}

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                # Simulates the buggy behavior — would block on a real
                # keep-alive socket. We approximate that by returning
                # nothing further so the test would hang if the helper
                # ever calls read() with no length cap.
                return b""
            chunk = self._body[self._pos:self._pos + n]
            self._pos += len(chunk)
            return chunk

    def test_reads_exact_content_length_and_doesnt_block(self):
        body = b'{"hello": "world"}'
        up = self._FakeUpstream(body, content_length=len(body))
        out = _read_response_bounded(up)
        self.assertEqual(out, body)

    def test_chunked_no_content_length_drains_until_empty(self):
        body = b'{"a":1,"b":2}'
        up = self._FakeUpstream(body, content_length=None)
        out = _read_response_bounded(up)
        self.assertEqual(out, body)

    def test_max_bytes_protects_from_runaway_upstream(self):
        # 1 MiB body, 64 KiB cap -> stops at 64 KiB.
        body = b"x" * (1024 * 1024)
        up = self._FakeUpstream(body, content_length=None)
        out = _read_response_bounded(up, max_bytes=64 * 1024)
        self.assertLessEqual(len(out), 64 * 1024)


class NimsNvextTests(unittest.TestCase):
    def test_nvext_thinking_in_response(self):
        oai = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "nvext": {"thinking": "let me think about this..."},
                },
                "finish_reason": "stop",
            }],
        }
        out = openai_to_anthropic_response(oai, "deepseek-ai/deepseek-v4-pro")
        # Reasoning surfaced as fallback because content was empty.
        self.assertEqual(len(out["content"]), 1)
        self.assertIn("let me think", out["content"][0]["text"])

    def test_endpoint_already_has_v1(self):
        # Defensive: if a caller passes "v1/chat/completions" we don't double up.
        self.assertEqual(_join_endpoint("https://api.deepseek.com", "v1/chat/completions"),
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(_join_endpoint("https://openrouter.ai/api/v1", "v1/chat/completions"),
                         "https://openrouter.ai/api/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
