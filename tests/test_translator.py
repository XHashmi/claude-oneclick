import json
import unittest

from claude_oneclick.proxy import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    _StreamTranslator,
    _join_endpoint,
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
        self.assertEqual(types[1], "content_block_start")
        # at least one delta
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

    def test_endpoint_already_has_v1(self):
        # Defensive: if a caller passes "v1/chat/completions" we don't double up.
        self.assertEqual(_join_endpoint("https://api.deepseek.com", "v1/chat/completions"),
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(_join_endpoint("https://openrouter.ai/api/v1", "v1/chat/completions"),
                         "https://openrouter.ai/api/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
