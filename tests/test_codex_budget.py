import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "plugins/context-budget/codex/budget.py"


class CodexBudgetTest(unittest.TestCase):
    def test_hint_gate_release_and_restore(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            transcript = directory / "rollout.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {"input_tokens": 120},
                                "model_context_window": 1000,
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(
                os.environ,
                PLUGIN_DATA=str(directory / "state"),
                TMPDIR=str(directory),
                CONTEXT_BUDGET_SOFT="100",
                CONTEXT_BUDGET_FIRM="150",
                CONTEXT_BUDGET_HARD="800",
                PYTHONDONTWRITEBYTECODE="1",
            )

            def call(name, **extra):
                event = {
                    "session_id": "test",
                    "transcript_path": str(transcript),
                    "hook_event_name": name,
                    **extra,
                }
                result = subprocess.run(
                    [sys.executable, str(SCRIPT)],
                    input=json.dumps(event),
                    text=True,
                    capture_output=True,
                    env=env,
                    check=True,
                )
                return json.loads(result.stdout)

            hint = call("PostToolUse")
            self.assertIn("handoff", hint["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(call("PreCompact", trigger="auto")["continue"], False)
            handoff = directory / "codex-context-budget/test.handoff.md"
            try:
                handoff.parent.mkdir(parents=True, exist_ok=True)
                handoff.write_text("Goal and next steps", encoding="utf-8")
                self.assertNotIn("continue", call("PreCompact", trigger="auto"))
                restored = call("SessionStart", source="compact")
                self.assertIn(
                    "Goal and next steps",
                    restored["hookSpecificOutput"]["additionalContext"],
                )
                self.assertFalse(handoff.exists())
            finally:
                handoff.unlink(missing_ok=True)
                handoff.with_suffix(".consumed").unlink(missing_ok=True)

    def test_missing_usage_fails_open(self):
        with tempfile.TemporaryDirectory() as root:
            event = {
                "session_id": "missing",
                "hook_event_name": "PreCompact",
                "transcript_path": str(Path(root) / "missing.jsonl"),
                "trigger": "auto",
            }
            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                input=json.dumps(event),
                text=True,
                capture_output=True,
                env=dict(os.environ, PLUGIN_DATA=root),
                check=True,
            )
            self.assertNotIn("continue", json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
