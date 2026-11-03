import unittest

from agent.llm import LLMClient


class _Stub(LLMClient):
    def __init__(self):
        self.seen = None

    def chat(self, system, messages, tools, max_tokens=1024):
        self.seen = (system, messages, tools)
        return {"role": "assistant", "content": [{"type": "text", "text": "hel"}, {"type": "tool_use", "id": "x", "name": "n", "input": {}}, {"type": "text", "text": "lo"}]}


class CompleteTests(unittest.TestCase):
    def test_complete_is_tool_free_and_returns_text_only(self):
        c = _Stub()
        self.assertEqual(c.complete("sys", "question"), "hello")
        self.assertEqual(c.seen[2], [])
        self.assertEqual(c.seen[1][0]["content"][0]["text"], "question")


if __name__ == "__main__":
    unittest.main()
