import json
import unittest

from claude_oneclick.proxy import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    _StreamTranslator,
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


if __name__ == "__main__":
    unittest.main()
