import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/context-budget"))
from memory.adapters import read_messages


class MemoryAdaptersTest(unittest.TestCase):
    def test_codex_discards_startup_messages_before_first_turn(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "codex.jsonl"
            records = [
                {"type": "response_item", "ordinal": 1, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<recommended_plugins>injected</recommended_plugins>"}]}},
                {"type": "turn_context", "payload": {"turn_id": "test"}},
                {"type": "response_item", "ordinal": 3, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "actual task"}]}},
                {"type": "response_item", "ordinal": 4, "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}},
                {"type": "turn_context", "payload": {"turn_id": "second"}},
            ]
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            self.assertEqual(read_messages(path, "codex"), [
                {"id": "codex:3", "text": "actual task"},
                {"id": "codex:4", "text": "done"},
            ])

    def test_codex_only_visible_user_and_assistant_text(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "codex.jsonl"
            records = [
                {"type": "response_item", "ordinal": 1, "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "secret"}]}},
                {"type": "response_item", "ordinal": 2, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "task"}]}},
                {"type": "response_item", "ordinal": 3, "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}},
                {"type": "response_item", "ordinal": 4, "payload": {"type": "custom_tool_call_output", "output": "tool secret"}},
            ]
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            self.assertEqual(read_messages(path, "codex"), [
                {"id": "codex:2", "text": "task"},
                {"id": "codex:3", "text": "done"},
            ])

    def test_claude_skips_thinking_tools_and_sidechains(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "claude.jsonl"
            records = [
                {"type": "user", "uuid": "u", "message": {"role": "user", "content": "task"}},
                {"type": "assistant", "uuid": "a", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": "done"}, {"type": "tool_use", "input": "secret"}]}},
                {"type": "assistant", "uuid": "sub", "isSidechain": True, "message": {"role": "assistant", "content": [{"type": "text", "text": "ignore"}]}},
            ]
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            self.assertEqual(read_messages(path, "claude"), [
                {"id": "claude:u", "text": "task"},
                {"id": "claude:a", "text": "done"},
            ])


if __name__ == "__main__":
    unittest.main()
