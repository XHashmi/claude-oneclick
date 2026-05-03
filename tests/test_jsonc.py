import unittest

from claude_oneclick.vscode import _parse_jsonc, _dump


class JsoncTests(unittest.TestCase):
    def test_strips_line_comments(self):
        text = '{\n  // a comment\n  "x": 1\n}\n'
        self.assertEqual(_parse_jsonc(text), {"x": 1})

    def test_strips_block_comments(self):
        text = '{\n  /* block\n     comment */\n  "y": [1, 2]\n}\n'
        self.assertEqual(_parse_jsonc(text), {"y": [1, 2]})

    def test_strips_trailing_commas(self):
        text = '{ "a": 1, "b": 2, }\n'
        self.assertEqual(_parse_jsonc(text), {"a": 1, "b": 2})

    def test_empty_text(self):
        self.assertEqual(_parse_jsonc(""), {})
        self.assertEqual(_parse_jsonc("   \n  "), {})

    def test_dump_roundtrip(self):
        d = {"a": 1, "b": {"c": 2}}
        self.assertEqual(_parse_jsonc(_dump(d)), d)


if __name__ == "__main__":
    unittest.main()
