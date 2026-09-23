import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "plugins/context-budget"


class MemoryHookBridgeTest(unittest.TestCase):
    def test_codex_capture_and_restore_without_handoff(self):
        self._exercise("codex", ROOT / "codex/budget.py")

    def test_claude_capture_and_restore_without_handoff(self):
        self._exercise("claude", ROOT / "scripts/budget.py")

    def _exercise(self, host, script):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            transcript = directory / "session.jsonl"
            if host == "codex":
                records = [
                    {"type": "response_item", "ordinal": 1, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "remember the goal"}]}},
                    {"type": "response_item", "ordinal": 2, "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "finished step one"}]}},
                ]
            else:
                records = [
                    {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "remember the goal"}},
                    {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [{"type": "text", "text": "finished step one"}]}},
                ]
            transcript.write_text("\n".join(json.dumps(x) for x in records), encoding="utf-8")
            env = dict(os.environ, CONTEXT_BUDGET_MEMORY="1", PLUGIN_DATA=str(directory / "codex-state"), CLAUDE_PLUGIN_DATA=str(directory / "claude-state"), PYTHONDONTWRITEBYTECODE="1")

            def hook(name, **extra):
                event = {"hook_event_name": name, "session_id": "bridge-test", "transcript_path": str(transcript), **extra}
                result = subprocess.run([sys.executable, str(script)], input=json.dumps(event), text=True, capture_output=True, env=env, check=True)
                return json.loads(result.stdout)

            hook("Stop")
            restored = hook("SessionStart", source="compact")
            content = restored["hookSpecificOutput"]["additionalContext"]
            self.assertIn("remember the goal", content)
            self.assertIn("finished step one", content)
            self.assertIn("Conversation history lookup", content)
            self.assertIn("history.py", content)
            hook("Stop")
            state_file = next((directory / (host + "-state") / "observational-memory").glob("*.json"))
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(len(state["observations"]), 2)


if __name__ == "__main__":
    unittest.main()
